"""Shared utilities for the TickTick MCP server.

Three concerns live here:

* Response formatting -- ``format_response`` turns Python values into the
  JSON strings that MCP clients consume, with a safety net for
  non-serialisable payloads.
* Auth gating -- ``require_ticktick_client`` short-circuits an MCP tool
  call when the singleton client could not authenticate.
* Cross-project task fetching -- ``_get_all_tasks_from_ticktick`` walks
  the user's projects and the inbox to collect every uncompleted task.

``_get_all_tasks_from_ticktick`` is a synchronous function; tool
implementations call it from inside async wrappers via direct invocation.
"""

import functools
import json
import logging
from datetime import date
from typing import Any, Optional

from .client import TickTickClientSingleton

logger = logging.getLogger(__name__)


class ToolLogicError(Exception):
    """Raised when an input fails our own validation.

    Distinct from ``ValueError`` so tool wrappers can distinguish "the
    caller asked for something we refuse to do" from errors raised by
    upstream libraries.
    """


def format_response(result: Any) -> str:
    """Serialise a Python value into a JSON string for MCP transport.

    * ``dict`` / ``list`` -> ``json.dumps`` with indent=2 and
      ``default=str`` so ``datetime``-like values do not crash the call.
    * ``None`` -> the JSON literal ``"null"``.
    * Anything else -> wrapped as ``{"result": str(value)}`` so the client
      always receives a JSON object.

    Serialisation errors are caught and converted to an error JSON object
    rather than re-raised.
    """
    try:
        if isinstance(result, (dict, list)):
            return json.dumps(result, indent=2, default=str)
        if result is None:
            return json.dumps(None)
        return json.dumps({"result": str(result)})
    except Exception as exc:
        logger.error("format_response failed: %s", exc, exc_info=True)
        return json.dumps(
            {
                "error": "Failed to serialize response",
                "details": str(exc),
            }
        )


def require_ticktick_client(func):
    """Decorator: short-circuit a tool call if the client is unavailable.

    Wraps an async MCP tool. When ``TickTickClientSingleton.get_client()``
    returns ``None`` (i.e. authentication failed), the wrapped coroutine
    is not called and we return a JSON error string straight to the MCP
    layer, including the underlying failure and when a retry will happen.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if TickTickClientSingleton.get_client() is None:
            if TickTickClientSingleton.is_rate_limited():
                message = (
                    "TickTick login is RATE LIMITED (HTTP 429). STOP -- do not"
                    " retry TickTick tools. Each call re-attempts the throttled"
                    " login endpoint and prolongs the block. Wait ~15-30 minutes"
                    " for the limit to clear, then try again. This is NOT a"
                    " credential problem."
                )
                return format_response(
                    {
                        "error": message,
                        "status": "rate_limited",
                        "retry": "stop; wait 15-30 min before any TickTick call",
                    }
                )
            detail = TickTickClientSingleton.last_error()
            message = "TickTick client not initialized"
            if detail:
                message += f" (last error: {detail})"
            message += (
                ". Initialisation is retried automatically after a cooldown"
                " -- wait a minute and call again. If this persists, check"
                " the server's .env credentials."
            )
            return format_response(
                {
                    "error": message,
                    "status": "error",
                }
            )
        return await func(*args, **kwargs)

    return wrapper


def _get_all_tasks_from_ticktick() -> list:
    """Aggregate every uncompleted task across the user's projects plus inbox.

    Walks ``client.state['projects']`` plus ``client.inbox_id`` and calls
    ``client.task.get_from_project`` for each. List responses are
    extended into the result; single-dict responses are appended as one
    item; ``None`` is skipped; other types log a warning.

    Per-project failures are caught and logged so one bad project does
    not abort the whole walk. Access to ``state`` or ``inbox_id`` is
    similarly wrapped so a broken client attribute does not crash.

    Raises:
        ConnectionError: when the singleton's client is ``None``.
    """
    client = TickTickClientSingleton.get_client()
    if client is None:
        raise ConnectionError("TickTick client is unavailable")

    project_ids: list[str] = []

    try:
        inbox_id = client.inbox_id
    except Exception as exc:
        logger.error("Could not read inbox_id: %s", exc)
        inbox_id = None
    if inbox_id:
        project_ids.append(inbox_id)

    try:
        projects = client.state.get("projects", []) or []
    except Exception as exc:
        logger.warning("Could not read state['projects']: %s", exc)
        projects = []

    for project in projects:
        pid = project.get("id") if isinstance(project, dict) else None
        if pid:
            project_ids.append(pid)

    all_tasks: list = []
    for pid in project_ids:
        try:
            tasks_in_project = client.task.get_from_project(pid)
        except Exception as exc:
            logger.warning("get_from_project(%s) failed: %s", pid, exc)
            continue

        if not tasks_in_project:
            continue
        if isinstance(tasks_in_project, list):
            all_tasks.extend(tasks_in_project)
        elif isinstance(tasks_in_project, dict):
            all_tasks.append(tasks_in_project)
        else:
            logger.warning(
                "Unexpected data type from get_from_project(%s): %s",
                pid,
                type(tasks_in_project),
            )

    return all_tasks


def _parse_due_date(date_str: Optional[Any]) -> Optional[date]:
    """Parse a TickTick dueDate string into a ``datetime.date``.

    TickTick stores due dates as ISO strings (often with a ``.000+0000``
    suffix). For coarse filtering we only need the calendar date, so we
    take the leading ``YYYY-MM-DD`` slice.

    Returns ``None`` and logs a warning when the input is too short,
    malformed, or not a string.
    """
    if date_str is None or date_str == "":
        return None
    if not isinstance(date_str, str):
        return None
    if len(date_str) < 10:
        logger.warning("_parse_due_date: input too short: %r", date_str)
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except ValueError as exc:
        logger.warning("_parse_due_date: Could not parse %r: %s", date_str, exc)
        return None
