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


def _augmented_check_status_code(response, error_message):
    """Drop-in for ticktick-py's ``check_status_code`` that includes the HTTP
    status code in the raised message.

    ticktick-py raises a bare ``RuntimeError('Could Not Complete Request')``
    with no status code, so a login rate-limit (HTTP 429) is indistinguishable
    downstream from any other failure. Including the code lets the singleton
    recognise a rate-limit and tell agents to stop retrying.
    """
    status = getattr(response, "status_code", None)
    if status != 200:
        if status is not None:
            raise RuntimeError(f"{error_message} (HTTP {status})")
        raise RuntimeError(error_message)


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
            oauth = OAuth2(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                cache_path=str(dotenv_dir_path / ".token-oauth"),
            )
            cls._instance = TickTickClient(
                username=USERNAME,
                password=PASSWORD,
                oauth=oauth,
            )
            cls._last_failure_monotonic = None
            cls._last_error = None
            logger.info("TickTick client initialised.")
        except Exception as exc:
            logger.error("Failed to initialise TickTick client: %s", exc, exc_info=True)
            cls._instance = None
            cls._last_failure_monotonic = time.monotonic()
            cls._last_error = str(exc)

        return cls._instance

    @classmethod
    def last_error(cls) -> Optional[str]:
        """Return the message of the most recent initialisation failure."""
        return cls._last_error

    @classmethod
    def is_rate_limited(cls) -> bool:
        """True when the most recent init failure was a login rate-limit.

        TickTick throttles the username/password ``user/signon`` endpoint
        with HTTP 429; the augmented ``check_status_code`` puts that code in
        the error message. Used to lengthen the retry backoff and to tell
        agents to stop retrying rather than hammer the throttled login.
        """
        return "429" in (cls._last_error or "")
