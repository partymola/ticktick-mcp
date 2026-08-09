"""Lazy singleton wrapping ``ticktick.api.TickTickClient``.

The ``ticktick-py`` client performs a username/password login plus a sync
during ``__init__``, so we keep a single instance for the lifetime of the
process and re-use it for every tool call. Construction failures are held
for a cooldown window (default 60s, env ``TICKTICK_MCP_INIT_RETRY_SECONDS``)
and then retried on the next request: a transient login failure (rate
limit, network blip) must not brick the server for its whole lifetime,
but each failed attempt costs seconds, so back-to-back tool calls should
not all re-attempt authentication.
"""

import json
import logging
import os
import time
from typing import Optional

from ticktick.api import TickTickClient
from ticktick.oauth2 import OAuth2

from .config import (
    CLIENT_ID,
    CLIENT_SECRET,
    PASSWORD,
    REDIRECT_URI,
    USERNAME,
    dotenv_dir_path,
)

logger = logging.getLogger(__name__)


class TickTickHTTPError(RuntimeError):
    """A TickTick request that did not return 200, carrying the status code.

    A ``RuntimeError`` subclass so existing handlers still catch it.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


#: Statuses that mean TickTick judged the credentials unusable. Everything
#: else - a rate limit, a 5xx, a timeout, a reset connection - clears on its
#: own, and answering it by discarding the session token re-runs the throttled
#: signon that the token cache exists to avoid.
_REJECTION_CODES = frozenset({401, 403})


def _augmented_check_status_code(response, error_message):
    """Drop-in for ticktick-py's ``check_status_code`` that records the status.

    ticktick-py raises a bare ``RuntimeError('Could Not Complete Request')``
    with no status code, so a login rate-limit is indistinguishable downstream
    from a rejected credential.
    """
    status = getattr(response, "status_code", None)
    if status != 200:
        if status is not None:
            raise TickTickHTTPError(f"{error_message} (HTTP {status})", status=status)
        raise TickTickHTTPError(error_message)


def _status_of(exc: BaseException) -> Optional[int]:
    """Return the HTTP status an exception carries, if any."""
    return getattr(exc, "status", None)


def _augment_check_status_code() -> None:
    """Install :func:`_augmented_check_status_code` on ticktick-py's client.

    Idempotent, and a no-op if the class has already been augmented. No extra
    HTTP request is made -- this only changes the exception message text.
    """
    if getattr(TickTickClient, "_status_code_augmented", False):
        return
    TickTickClient.check_status_code = staticmethod(_augmented_check_status_code)
    TickTickClient._status_code_augmented = True


_augment_check_status_code()


# ---------------------------------------------------------------------------
# Cached v2 session token
#
# ticktick-py re-runs a full username/password login (POST user/signon) on
# every client construction. TickTick throttles that endpoint with HTTP 429,
# so a server that re-logs-in on every start eventually gets locked out -- a
# browser avoids this by keeping its session cookie and never re-submitting
# credentials. We do the same: persist the v2 session token from a successful
# login and inject it on the next construction, skipping signon entirely. A
# stale token simply fails the follow-up _settings()/sync() calls, which
# triggers a single real login that refreshes the cache.
# ---------------------------------------------------------------------------

_V2_TOKEN_FILENAME = ".token-v2"
_INJECTED_V2_TOKEN: Optional[str] = None


def _v2_token_path():
    return dotenv_dir_path / _V2_TOKEN_FILENAME


def _read_v2_token() -> Optional[str]:
    """Return the cached v2 session token, or ``None`` if absent/unreadable.

    An unreadable cache is deleted so the next call rebuilds it. Without
    that, a file that cannot be decoded is unrecoverable without hand-
    deleting a file the docs tell users to keep.
    """
    try:
        raw = _v2_token_path().read_text()
    except OSError:
        return None
    except ValueError:
        _delete_v2_token()
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        _delete_v2_token()
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token or None


def _write_v2_token(token: Optional[str]) -> None:
    """Persist the v2 session token with 0600 perms; best-effort, no raise.

    Only genuine string tokens are written -- this keeps a mocked client's
    non-string ``access_token`` from being serialised in tests.
    """
    if not isinstance(token, str) or not token:
        return
    path = _v2_token_path()
    try:
        # 0600 at creation, not chmodded after: a write-then-chmod leaves the
        # token in a world-readable file for the duration of every refresh,
        # and leaves it there permanently if the chmod fails.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except (OSError, AttributeError):
                logger.warning("Could not tighten permissions on the cached v2 token")
            handle.write(json.dumps({"token": token}))
    except OSError as exc:
        logger.warning("Could not cache TickTick v2 token: %s", exc)


def _tighten(path) -> None:
    """Narrow a credential file to owner-only; best-effort, no raise."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("Could not tighten permissions on %s", getattr(path, "name", path))


def _delete_v2_token() -> None:
    """Remove the cached v2 session token; best-effort, no raise."""
    try:
        _v2_token_path().unlink()
    except OSError:
        pass


def _augment_login() -> None:
    """Let ticktick-py reuse a cached v2 token instead of hitting signon.

    Wraps ``TickTickClient._login`` once so that, when a token has been
    injected (via :meth:`TickTickClientSingleton._construct_client`), it sets
    the session token directly and skips the throttled username/password POST.
    With no injected token it falls back to the original login unchanged.
    """
    if getattr(TickTickClient, "_login_augmented", False):
        return
    original_login = TickTickClient._login

    def _login(self, username, password):
        if _INJECTED_V2_TOKEN:
            self.access_token = _INJECTED_V2_TOKEN
            self.cookies["t"] = _INJECTED_V2_TOKEN
            return
        original_login(self, username, password)

    TickTickClient._login = _login
    TickTickClient._login_augmented = True


_augment_login()


_DEFAULT_INIT_RETRY_SECONDS = 60.0
_DEFAULT_RATELIMIT_RETRY_SECONDS = 300.0


def _init_retry_seconds() -> float:
    """Resolve the cooldown, honouring ``TICKTICK_MCP_INIT_RETRY_SECONDS``."""
    raw = os.environ.get("TICKTICK_MCP_INIT_RETRY_SECONDS")
    if raw is None:
        return _DEFAULT_INIT_RETRY_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid TICKTICK_MCP_INIT_RETRY_SECONDS=%r; using default", raw)
        return _DEFAULT_INIT_RETRY_SECONDS
    return value if value >= 0 else _DEFAULT_INIT_RETRY_SECONDS


INIT_RETRY_SECONDS: float = _init_retry_seconds()


def _ratelimit_retry_seconds() -> float:
    """Resolve the rate-limit backoff, honouring
    ``TICKTICK_MCP_RATELIMIT_RETRY_SECONDS``.

    Longer than the ordinary init cooldown because a 429 clears slowly and
    every re-attempt of the throttled login prolongs it.
    """
    raw = os.environ.get("TICKTICK_MCP_RATELIMIT_RETRY_SECONDS")
    if raw is None:
        return _DEFAULT_RATELIMIT_RETRY_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid TICKTICK_MCP_RATELIMIT_RETRY_SECONDS=%r; using default", raw)
        return _DEFAULT_RATELIMIT_RETRY_SECONDS
    return value if value >= 0 else _DEFAULT_RATELIMIT_RETRY_SECONDS


RATELIMIT_RETRY_SECONDS: float = _ratelimit_retry_seconds()


class TickTickClientSingleton:
    """Module-level container for the shared ``TickTickClient`` instance."""

    _instance: Optional[TickTickClient] = None
    _last_failure_monotonic: Optional[float] = None
    _last_error: Optional[str] = None
    _last_status: Optional[int] = None
    #: One shape-based cache clear per process. See _is_rejection.
    _shape_recovery_used: bool = False

    @classmethod
    def get_client(cls) -> Optional[TickTickClient]:
        """Return the shared client, constructing it on first call.

        Returns ``None`` (and logs the error) if authentication fails.
        Calls within ``INIT_RETRY_SECONDS`` of a failure also return
        ``None`` without re-attempting; after the cooldown the next call
        retries construction.
        """
        if cls._instance is not None:
            return cls._instance

        if cls._last_failure_monotonic is not None:
            elapsed = time.monotonic() - cls._last_failure_monotonic
            cooldown = RATELIMIT_RETRY_SECONDS if cls.is_rate_limited() else INIT_RETRY_SECONDS
            if elapsed < cooldown:
                return None
            logger.info(
                "Retrying TickTick client initialisation (%.0fs since last failure).",
                elapsed,
            )

        try:
            cls._instance = cls._construct_client()
            cls._last_failure_monotonic = None
            cls._last_error = None
            cls._last_status = None
            logger.info("TickTick client initialised.")
        except Exception as exc:
            logger.error("Failed to initialise TickTick client: %s", exc, exc_info=True)
            cls._instance = None
            cls._last_failure_monotonic = time.monotonic()
            cls._last_error = str(exc)
            cls._last_status = _status_of(exc)

        return cls._instance

    @classmethod
    def last_error(cls) -> Optional[str]:
        """Return the message of the most recent initialisation failure."""
        return cls._last_error

    @classmethod
    def is_rate_limited(cls) -> bool:
        """True when the most recent init failure was a login rate-limit.

        TickTick throttles the username/password ``user/signon`` endpoint with
        HTTP 429. Read from the status the exception carries, never from the
        message text: a URL or a task id containing "429" would otherwise put
        the server into a five-minute cooldown and tell the agent to stop.
        """
        return cls._last_status == 429

    @classmethod
    def _is_rejection(cls, exc: BaseException) -> bool:
        """True when the cached token is the thing that failed.

        A status in _REJECTION_CODES says so outright. A response of a shape
        the client cannot read says so only circumstantially, so that branch
        fires at most once per process: a genuine schema change would
        otherwise cost a signon on every cooldown, on the endpoint the cache
        exists to protect.
        """
        if _status_of(exc) in _REJECTION_CODES:
            return True
        if isinstance(exc, (KeyError, TypeError, AttributeError)) and not cls._shape_recovery_used:
            cls._shape_recovery_used = True
            return True
        return False

    @classmethod
    def _construct_client(cls) -> TickTickClient:
        """Build a ``TickTickClient``, reusing a cached v2 session token when
        possible to avoid the throttled signon endpoint.

        A cached token is injected and the client's own ``_settings()``/
        ``sync()`` startup calls then exercise it. Only a rejection clears the
        cache and runs a fresh username/password login: either a status in
        :data:`_REJECTION_CODES`, or a response of an unusable shape while a
        token was injected. Every other failure propagates with the cache
        intact, so a rate limit or a dropped connection cannot cost a working
        session token and re-run the throttled signon. With no cache, a fresh
        login runs and its token is cached for next time.

        The shape case is a deliberate belt and it is bounded to one attempt
        per process by :data:`_shape_recovery_used`. What TickTick answers to
        a stale session cookie is not established here - if it is a 200 with
        an unexpected body rather than a 401, nothing else would ever clear
        the cache and every retry would fail identically. Being wrong the
        other way, on a genuine schema change, costs one signon rather than
        one per cooldown, which is why the flag exists.
        """
        global _INJECTED_V2_TOKEN

        def _new_oauth() -> OAuth2:
            cache_path = dotenv_dir_path / ".token-oauth"

            def _build() -> OAuth2:
                return OAuth2(
                    client_id=CLIENT_ID,
                    client_secret=CLIENT_SECRET,
                    redirect_uri=REDIRECT_URI,
                    cache_path=str(cache_path),
                )

            try:
                oauth = _build()
            except ValueError:
                # ticktick-py's cache reader catches only IOError, so a file
                # that is not JSON or not UTF-8 escapes and nothing clears it.
                # Deleted and retried exactly once, and only around the
                # construction - rebuilding this file needs a person at a
                # browser, so a broad catch here would turn a transient
                # failure into a mandatory re-authorisation.
                logger.warning("Cached OAuth token is unreadable; discarding it.")
                try:
                    cache_path.unlink()
                except OSError:
                    pass
                oauth = _build()

            # ticktick-py writes this file with no mode of its own, so a
            # bearer token lands at whatever the umask gives.
            _tighten(cache_path)
            return oauth

        cached = _read_v2_token()
        if cached:
            _INJECTED_V2_TOKEN = cached
            try:
                client = TickTickClient(username=USERNAME, password=PASSWORD, oauth=_new_oauth())
                logger.info("TickTick session resumed from cached v2 token.")
                return client
            except Exception as exc:
                if not cls._is_rejection(exc):
                    raise
                logger.info(
                    "Cached TickTick v2 token rejected (%s); logging in fresh.",
                    type(exc).__name__,
                )
                _delete_v2_token()
            finally:
                _INJECTED_V2_TOKEN = None

        client = TickTickClient(username=USERNAME, password=PASSWORD, oauth=_new_oauth())
        _write_v2_token(getattr(client, "access_token", None))
        return client
