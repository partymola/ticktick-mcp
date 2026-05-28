"""Task filtering tool and supporting filter primitives.

This module exposes:

* ``PeriodFilter`` -- a ``[start, end]`` window with parser quirks
  documented in the tests. The validator emits naive ``datetime``
  objects (it never sees the ``tz`` field thanks to pydantic field
  ordering); ``_parse_task_date`` may also return ``None`` for a naive
  task date paired with a filter ``tz`` because ``ZoneInfo`` has no
  ``localize`` method (the bare-except path).
* ``PropertyFilter`` -- aggregates project/priority/tag/status criteria
  and the date filter that applies based on status.
* ``TaskFilterer`` -- orchestrates fetch + filter + optional sort.
* ``_build_property_filter`` -- maps the agent-facing dict (with keys
  like ``due_start_date``) onto the filter objects.
* ``ticktick_filter_tasks`` -- the MCP entry point.

Status uses TickTick's wire value (``2`` = completed) so filtering
completed tasks works regardless of where they came from.
"""

import datetime
import json
import logging
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from ..client import TickTickClientSingleton
from ..helpers import (
    _get_all_tasks_from_ticktick,
    format_response,
    require_ticktick_client,
)
from ..mcp_instance import mcp

logger = logging.getLogger(__name__)


# --- Constants ---

_COMPLETED_STATUS = 2  # TickTick API: status == 2 means completed
_VALID_STATUSES = {"uncompleted", "completed"}


# --- PeriodFilter ---


class PeriodFilter(BaseModel):
    """A date window with optional tz context.

    ``start_date`` and ``end_date`` are stored as naive ``datetime``
    objects. The validator never sees ``tz`` (it is declared after the
    date fields and pydantic v1-style validators run in declaration
    order), so the result is naive even when a tz is supplied -- this
    is the documented behaviour the test suite pins. ``contains()``
    compares at date-granularity and treats no-bound filters as
    "match anything".
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    start_date: Optional[datetime.datetime] = None
    end_date: Optional[datetime.datetime] = None
    tz: Optional[ZoneInfo] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _format_time(cls, value: Any) -> Any:
        """Parse the input into a naive ``datetime`` or ``None``.

        - ``None`` and empty string -> ``None``.
        - Unparseable string -> ``None``.
        - tz-aware datetime -> stripped to local naive (``.astimezone(None)``).
        - Naive string with no tz info -> returned as-is, naive.
        """
        if value is None or value == "":
            return None
        if isinstance(value, datetime.datetime):
            dt = value
        elif isinstance(value, datetime.date):
            return datetime.datetime(value.year, value.month, value.day)
        elif isinstance(value, str):
            cleaned = value
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            try:
                dt = datetime.datetime.fromisoformat(cleaned)
            except ValueError:
                # Try a bare date prefix.
                try:
                    return datetime.datetime.fromisoformat(cleaned[:10])
                except ValueError:
                    return None
        else:
            return None

        # If the parsed datetime carries tz info, convert to local time
        # and strip the tzinfo so we end up naive (documented behaviour
        # the test suite pins).
        if dt.tzinfo is not None:
            try:
                local = dt.astimezone()
                return local.replace(tzinfo=None)
            except Exception:
                return None
        return dt

    @field_validator("tz", mode="before")
    @classmethod
    def _coerce_tz(cls, value: Any) -> Optional[ZoneInfo]:
        if value is None or isinstance(value, ZoneInfo):
            return value
        if isinstance(value, str):
            try:
                return ZoneInfo(value)
            except ZoneInfoNotFoundError:
                logger.warning("Unknown timezone: %s", value)
                return None
        return value

    def _parse_task_date(self, date_str: Optional[str]) -> Optional[datetime.datetime]:
        """Parse a TickTick task date string into a ``datetime``.

        - Empty / None -> ``None``.
        - Strings with millisecond suffix (``.000``) and ``Z``-style or
          compact-offset suffixes are accepted.
        - When ``self.tz`` is set:
            * If the parsed datetime is naive, we call
              ``self.tz.localize(dt)`` -- a pytz-style API the test
              suite pins as the documented behaviour. ``ZoneInfo`` has
              no ``localize`` method, so this raises AttributeError;
              the bare except swallows it and returns ``None``.
            * If the parsed datetime is tz-aware, we convert it to
              ``self.tz`` and keep the tz info.
        - When ``self.tz`` is None and the parsed datetime is tz-aware,
          we strip to local naive.
        """
        if not date_str or not isinstance(date_str, str):
            return None
        try:
            cleaned = date_str
            # Strip ".000" millisecond suffix if present (TickTick).
            if "." in cleaned and "+" in cleaned:
                head, sep, tail = cleaned.partition(".")
                # tail is something like "000+0000" -> drop millis
                if len(tail) >= 3 and tail[:3].isdigit():
                    cleaned = head + tail[3:]
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            # fromisoformat supports compact offsets like "+0000" in 3.11+
            try:
                dt = datetime.datetime.fromisoformat(cleaned)
            except ValueError:
                # Bare date prefix fallback.
                dt = datetime.datetime.fromisoformat(cleaned[:10])
        except Exception as exc:
            logger.debug("_parse_task_date: parse failed for %r: %s", date_str, exc)
            return None

        try:
            if self.tz is not None:
                if dt.tzinfo is None:
                    # Documented behaviour: call .localize(dt) on the
                    # tz. ZoneInfo has no .localize, so this raises
                    # AttributeError and the outer except returns None.
                    return self.tz.localize(dt)  # type: ignore[attr-defined]
                return dt.astimezone(self.tz)
            # No filter tz: strip to local naive if needed.
            if dt.tzinfo is not None:
                return dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception as exc:
            logger.debug("_parse_task_date: tz step failed for %r: %s", date_str, exc)
            return None

    def contains(self, date_str: Optional[str]) -> bool:
        """Return True if ``date_str`` falls inside this window.

        With no bounds set, contains() returns ``True`` for any input
        (including unparseable ones). With bounds set, an unparseable
        task date is treated as "not in window" (False).
        """
        has_bounds = self.start_date is not None or self.end_date is not None
        task_dt = self._parse_task_date(date_str)
        if task_dt is None:
            return not has_bounds

        task_date = task_dt.date() if hasattr(task_dt, "date") else task_dt
        if self.start_date is not None:
            if task_date < self.start_date.date():
                return False
        if self.end_date is not None:
            if task_date > self.end_date.date():
                return False
        return True


# --- PropertyFilter ---


class PropertyFilter(BaseModel):
    """Aggregate of all the per-task criteria a caller can specify."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tag_label: Optional[str] = None
    project_id: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None  # "uncompleted" or "completed"
    due_date_filter: Optional[PeriodFilter] = None
    completion_date_filter: Optional[PeriodFilter] = None

    def matches(self, task: dict) -> bool:
        """Return True if ``task`` satisfies every set criterion."""
        if not isinstance(task, dict):
            return False

        if self.project_id is not None and task.get("projectId") != self.project_id:
            return False

        if self.priority is not None and task.get("priority") != self.priority:
            return False

        if self.tag_label is not None:
            tags = task.get("tags") or []
            if self.tag_label not in tags:
                return False

        task_status = task.get("status", 0)
        if self.status == "completed" and task_status != _COMPLETED_STATUS:
            return False
        if self.status == "uncompleted" and task_status == _COMPLETED_STATUS:
            return False

        # Choose date filter based on lifecycle stage.
        if self.status == "completed":
            if self.completion_date_filter is not None:
                if not self.completion_date_filter.contains(task.get("completedTime")):
                    return False
        else:
            if self.due_date_filter is not None:
                if not self.due_date_filter.contains(task.get("dueDate")):
                    return False

        return True


# --- TaskFilterer ---


class TaskFilterer:
    """Orchestrates fetch + filter + optional sort for a property filter."""

    async def _fetch_tasks_by_status(
        self,
        status: Optional[str],
        completion_date_filter: Optional[PeriodFilter],
        tz_info: Optional[ZoneInfo],
    ) -> list[dict]:
        """Fetch the candidate task set from TickTick.

        - ``status == "completed"``: requires a ``completion_date_filter``
          with at least one bound. Calls ``client.task.get_completed`` and
          re-applies the period filter for precise trimming. Any
          underlying error is wrapped in ``ConnectionError``.
        - Otherwise: walks every project for uncompleted tasks via
          ``_get_all_tasks_from_ticktick``.
        """
        if status == "completed":
            if completion_date_filter is None:
                return []
            if (
                completion_date_filter.start_date is None
                and completion_date_filter.end_date is None
            ):
                return []

            try:
                client = TickTickClientSingleton.get_client()
                if client is None:
                    raise ConnectionError("TickTick client is unavailable")

                tz_name: Optional[str] = None
                tz_source = completion_date_filter.tz or tz_info
                if tz_source is not None:
                    tz_name = getattr(tz_source, "key", None) or str(tz_source)

                kwargs: dict[str, Any] = {
                    "start": completion_date_filter.start_date,
                    "end": completion_date_filter.end_date,
                }
                if tz_name:
                    kwargs["tz"] = tz_name
                tasks = client.task.get_completed(**kwargs)
            except ConnectionError:
                raise
            except Exception as exc:
                raise ConnectionError(
                    f"Failed to fetch completed tasks: {exc}"
                ) from exc

            if tasks is None:
                return []
            if isinstance(tasks, dict):
                tasks = [tasks]
            # Re-apply the window for precision (API filter is by day).
            return [t for t in tasks if completion_date_filter.contains(
                t.get("completedTime")
            )]

        # Uncompleted -> walk projects.
        return _get_all_tasks_from_ticktick()

    async def filter(
        self,
        property_filter: PropertyFilter,
        sort_by_priority: bool = False,
        tz_info: Optional[ZoneInfo] = None,
    ) -> list[dict]:
        """Fetch and filter tasks. Optionally sort by descending priority."""
        candidates = await self._fetch_tasks_by_status(
            status=property_filter.status,
            completion_date_filter=property_filter.completion_date_filter,
            tz_info=tz_info,
        )
        matched = [task for task in candidates if property_filter.matches(task)]
        if sort_by_priority:
            matched.sort(key=lambda t: t.get("priority", 0) or 0, reverse=True)
        return matched


# --- _build_property_filter ---


def _build_property_filter(
    filter_criteria: Any,
) -> tuple[PropertyFilter, Optional[ZoneInfo], bool]:
    """Translate the agent-facing dict into our internal filter objects.

    Returns ``(property_filter, tz_info, sort_by_priority)``.

    Raises ``ValueError`` on malformed input.

    Recognised keys:
        - ``status``: ``"uncompleted"`` (default) or ``"completed"``.
        - ``project_id``, ``tag_label``, ``priority``.
        - ``due_start_date``, ``due_end_date`` -- build the due-date
          ``PeriodFilter``.
        - ``completion_start_date``, ``completion_end_date`` -- build
          the completion-date ``PeriodFilter``.
        - ``tz`` -- IANA name, applied to both date filters.
        - ``sort_by_priority`` (bool).
    """
    if isinstance(filter_criteria, str):
        try:
            filter_criteria = json.loads(filter_criteria)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON string: {exc}") from exc

    if not isinstance(filter_criteria, dict):
        raise ValueError("filter_criteria must be a JSON object or JSON string")

    status = filter_criteria.get("status", "uncompleted")
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be 'uncompleted' or 'completed'."
        )

    tz_name = filter_criteria.get("tz")
    tz_info: Optional[ZoneInfo] = None
    if isinstance(tz_name, str) and tz_name:
        try:
            tz_info = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown timezone in filter_criteria: %s", tz_name)
            tz_info = None

    due_filter = PeriodFilter(
        start_date=filter_criteria.get("due_start_date"),
        end_date=filter_criteria.get("due_end_date"),
        tz=tz_info,
    )
    completion_filter = PeriodFilter(
        start_date=filter_criteria.get("completion_start_date"),
        end_date=filter_criteria.get("completion_end_date"),
        tz=tz_info,
    )

    sort_by_priority = bool(filter_criteria.get("sort_by_priority", False))

    property_filter = PropertyFilter(
        tag_label=filter_criteria.get("tag_label"),
        project_id=filter_criteria.get("project_id"),
        priority=filter_criteria.get("priority"),
        status=status,
        due_date_filter=due_filter,
        completion_date_filter=completion_filter,
    )

    return property_filter, tz_info, sort_by_priority


# --- Public MCP tool ---


@mcp.tool()
@require_ticktick_client
async def ticktick_filter_tasks(filter_criteria: Any) -> str:
    """Return the tasks matching every supplied filter criterion.

    Supports any combination of project, priority, tag, status, and a
    date window applied to either the due date (open tasks) or the
    completion timestamp (completed tasks).

    Args:
        filter_criteria (dict | str): A criteria object, or a JSON string
            that decodes to one. Recognised keys:

            * ``status``: ``"uncompleted"`` (default) or ``"completed"``.
              When ``"completed"`` you should supply
              ``completion_start_date`` and/or ``completion_end_date``;
              without dates the result is an empty list.
            * ``project_id`` (str): Limit to tasks in this project.
            * ``priority`` (int): 0=None, 1=Low, 3=Medium, 5=High.
            * ``tag_label`` (str): Tag name (case-sensitive).
            * ``due_start_date`` / ``due_end_date`` (str): ISO date or
              datetime strings; only used when ``status='uncompleted'``.
            * ``completion_start_date`` / ``completion_end_date`` (str):
              ISO date or datetime strings; only used when
              ``status='completed'``.
            * ``tz`` (str): Default IANA timezone applied to date filters.
            * ``sort_by_priority`` (bool): Sort by descending priority.

    Returns:
        JSON list of matching task objects. Empty list if nothing
        matches. On invalid input or backend failure:
        ``{"error": "...", "status": "error"}``.

    Limitations:
        - TickTick caps ``get_completed`` at 100 results; very wide
          completion windows are truncated server-side.
        - Filtering happens client-side after the fetch, so additional
          criteria do not reduce the number of network requests.

    Agent Usage Guide:
        - List open tasks in a project:
            ``{"status": "uncompleted", "project_id": "<id>"}``
        - List completed tasks in the last 7 days:
            ``{
                "status": "completed",
                "project_id": "<id>",
                "completion_start_date": "2026-05-21",
                "completion_end_date":   "2026-05-28"
            }``
        - Find high-priority open tasks due this month, sorted:
            ``{
                "priority": 5,
                "due_start_date": "2026-05-01",
                "due_end_date":   "2026-05-31",
                "sort_by_priority": true
            }``
    """
    try:
        property_filter, tz_info, sort_by_priority = _build_property_filter(
            filter_criteria
        )
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})

    try:
        results = await TaskFilterer().filter(
            property_filter=property_filter,
            sort_by_priority=sort_by_priority,
            tz_info=tz_info,
        )
    except ConnectionError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except Exception as exc:
        logger.error("ticktick_filter_tasks: unexpected error: %s", exc, exc_info=True)
        return format_response(
            {"error": f"unexpected error: {exc}", "status": "error"}
        )

    return format_response(results)
