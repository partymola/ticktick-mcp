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

_DEFAULT_INIT_RETRY_SECONDS = 60.0


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
            if elapsed < INIT_RETRY_SECONDS:
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
