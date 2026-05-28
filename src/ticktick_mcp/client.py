"""Lazy singleton wrapping ``ticktick.api.TickTickClient``.

The ``ticktick-py`` client performs a username/password login plus a sync
during ``__init__``, so we keep a single instance for the lifetime of the
process and re-use it for every tool call. If construction fails, we cache
the failure so each subsequent request returns ``None`` immediately rather
than re-attempting authentication.
"""

import logging
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


class TickTickClientSingleton:
    """Module-level container for the shared ``TickTickClient`` instance."""

    _instance: Optional[TickTickClient] = None
    _initialised: bool = False

    @classmethod
    def get_client(cls) -> Optional[TickTickClient]:
        """Return the shared client, constructing it on first call.

        Returns ``None`` (and logs the error) if authentication fails.
        Subsequent calls after a failure also return ``None`` -- the
        singleton does not retry.
        """
        if cls._initialised:
            return cls._instance

        cls._initialised = True
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
            logger.info("TickTick client initialised.")
        except Exception as exc:  # pragma: no cover - exercised in production only
            logger.error("Failed to initialise TickTick client: %s", exc, exc_info=True)
            cls._instance = None

        return cls._instance
