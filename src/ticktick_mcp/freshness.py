"""Throttled freshness control for the read and mutation tools.

The ``ticktick-py`` client syncs its local ``state`` only once, at
construction (see ``client.py``). Because the MCP server is a long-lived
process and is not the only writer (the same tasks are edited in the
TickTick app on other devices), that cache goes stale: a task created or
reopened in the app stays invisible to the active-read tools until the
process restarts. ``ensure_fresh`` closes that gap by syncing on demand,
throttled so a burst of reads in one turn does not trigger a full-account
batch GET per call.

This module is intentionally import-light (stdlib only) -- like
``verification.py`` it pulls in no package-internal modules, so it can be
unit-tested without the client/config import chain. Callers pass the
already-constructed client.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Default throttle window. A burst of reads inside one agent turn collapses
# to a single sync; a fast agent still never sees data older than this.
# Overridable via the environment for tuning.
_DEFAULT_TTL_SECONDS = 15.0

# After a sync failure, wait at least this long before a non-forced caller
# tries again, so a rate-limit / outage does not turn every read into a
# tight resync loop.
_FAIL_BACKOFF_SECONDS = 5.0

# Module-level throttle state. One MCP server == one process, so process-wide
# state is correct in production; tests reset it via reset_freshness_state().
_last_sync_monotonic: Optional[float] = None
_last_fail_monotonic: Optional[float] = None


def _ttl_seconds() -> float:
    """Resolve the TTL, honouring ``TICKTICK_MCP_SYNC_TTL_SECONDS``."""
    raw = os.environ.get("TICKTICK_MCP_SYNC_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_TTL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid TICKTICK_MCP_SYNC_TTL_SECONDS=%r; using default", raw)
        return _DEFAULT_TTL_SECONDS
    return value if value >= 0 else _DEFAULT_TTL_SECONDS


def reset_freshness_state() -> None:
    """Clear the throttle timestamps. For test isolation only."""
    global _last_sync_monotonic, _last_fail_monotonic
    _last_sync_monotonic = None
    _last_fail_monotonic = None


def ensure_fresh(client, force: bool = False) -> bool:
    """Refresh the client's local state from the server, subject to a throttle.

    Args:
        client: the live ``TickTickClient`` (or ``None``).
        force: bypass the TTL and the post-failure backoff and sync now.
            Used before the read step of a mutation, and by the explicit
            ``ticktick_sync`` tool.

    Returns:
        ``True`` if the local state is fresh after the call -- either a sync
        just succeeded, or a successful sync ran within the TTL and ``force``
        was not set. ``False`` if a sync was attempted and failed (the
        caller should serve the last-known state as stale-but-usable) or if
        ``client`` is ``None``.

    Never raises: sync failures are logged and reported via the return value
    so a transient API problem degrades reads to stale data rather than
    erroring the tool call.
    """
    global _last_sync_monotonic, _last_fail_monotonic

    if client is None:
        return False

    now = time.monotonic()

    if not force:
        if _last_sync_monotonic is not None and (now - _last_sync_monotonic) < _ttl_seconds():
            return True
        if (
            _last_fail_monotonic is not None
            and (now - _last_fail_monotonic) < _FAIL_BACKOFF_SECONDS
        ):
            return False

    try:
        client.sync()
    except Exception as exc:  # network / rate-limit / transient API error
        _last_fail_monotonic = time.monotonic()
        logger.warning("ensure_fresh: sync failed, serving stale state: %s", exc)
        return False

    _last_sync_monotonic = time.monotonic()
    _last_fail_monotonic = None
    return True
