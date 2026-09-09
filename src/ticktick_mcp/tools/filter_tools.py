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
from ..compact import DETAIL_COMPACT, normalise_detail, render_task_list
from ..freshness import ensure_fresh
from ..helpers import (
    ToolLogicError,
    _get_all_tasks_from_ticktick,
    format_response,
    require_ticktick_client,
)
from ..mcp_instance import mcp
from ..projects import is_known_project_id, resolve_project_id

logger = logging.getLogger(__name__)


# --- Constants ---

_COMPLETED_STATUS = 2  # TickTick API: status == 2 means completed
_VALID_STATUSES = {"uncompleted", "completed"}
_VALID_PRIORITIES = (0, 1, 3, 5)  # TickTick stores nothing between these
_DATE_KEYS = (
    "due_start_date",
    "due_end_date",
    "completion_start_date",
    "completion_end_date",
)
_RECOGNISED_KEYS = frozenset(
    ("status", "project_id", "priority", "tag_label", "tz", "sort_by_priority") + _DATE_KEYS
)


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
                raise ConnectionError(f"Failed to fetch completed tasks: {exc}") from exc

            if tasks is None:
                return []
            if isinstance(tasks, dict):
                tasks = [tasks]
            # Re-apply the window for precision (API filter is by day).
            return [t for t in tasks if completion_date_filter.contains(t.get("completedTime"))]

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


# --- Criteria validation ---


def _reject_unknown_keys(criteria: dict) -> None:
    unknown = sorted(str(key) for key in criteria if key not in _RECOGNISED_KEYS)
    if unknown:
        raise ValueError(
            f"Unrecognised filter criteria: {', '.join(repr(k) for k in unknown)}. "
            f"Recognised: {', '.join(sorted(_RECOGNISED_KEYS))}."
        )


def _checked_priority(value: Any) -> Optional[int]:
    """Return one of TickTick's four priorities, or ``None`` if unset."""
    if value is None:
        return None
    # bool is an int subclass, so True would otherwise pass as Low.
    if isinstance(value, bool) or not isinstance(value, int) or value not in _VALID_PRIORITIES:
        raise ValueError(
            f"Invalid priority {value!r}. TickTick uses "
            f"{', '.join(str(p) for p in _VALID_PRIORITIES)} (none, low, medium, high)."
        )
    return value


def _checked_tz(value: Any) -> Optional[ZoneInfo]:
    """Return the zone named, or refuse a name that does not resolve.

    Trimmed like the text criteria, so a padded name is a zone rather than
    a refusal.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Blank means unset, matching an absent key. `_checked_text` refuses a
        # blank instead, because there a filter value is what is missing.
        if not value.strip():
            return None
        try:
            return ZoneInfo(value.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown timezone {value!r}. Expected an IANA name, such as 'Europe/London'."
            ) from exc
    raise ValueError(
        f"Invalid tz {value!r}. Expected an IANA timezone name, such as 'Europe/London'."
    )


def _checked_text(criteria: dict, key: str) -> Optional[str]:
    """Return a non-empty, trimmed string criterion, or ``None`` if unset.

    Trimmed, not merely checked: `tag_label` is matched by exact membership
    against a task's tags, so a padded value would match nothing and answer
    with the empty list this whole check exists to prevent.
    """
    value = criteria.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid {key} {value!r}. Expected a non-empty string.")
    return value.strip()


def _checked_sort_flag(criteria: dict) -> bool:
    value = criteria.get("sort_by_priority", False)
    # None means "unset" for every other criterion, so it means it here too.
    if value is None:
        return False
    if not isinstance(value, bool):
        # bool("false") is True, so the string form would silently sort.
        raise ValueError(f"Invalid sort_by_priority {value!r}. Expected true or false.")
    return value


def _reject_unparsed_dates(
    criteria: dict,
    due_filter: PeriodFilter,
    completion_filter: PeriodFilter,
) -> None:
    """Refuse a bound that did not parse, and a window that ends before it
    begins. Both otherwise run as a query that can match nothing."""
    parsed = {
        "due_start_date": due_filter.start_date,
        "due_end_date": due_filter.end_date,
        "completion_start_date": completion_filter.start_date,
        "completion_end_date": completion_filter.end_date,
    }
    for key in _DATE_KEYS:
        raw = criteria.get(key)
        if raw is None or raw == "":
            continue
        if parsed[key] is None:
            raise ValueError(
                f"Invalid {key} {raw!r}. Expected an ISO date or datetime, such as '2026-09-01'."
            )

    for label, window in (("due", due_filter), ("completion", completion_filter)):
        start, end = window.start_date, window.end_date
        # Compared as dates, not datetimes, because contains() is day-granular:
        # a window whose times run backwards inside one day still means that day.
        if start is not None and end is not None and start.date() > end.date():
            raise ValueError(
                f"{label}_start_date {criteria[f'{label}_start_date']!r} is after "
                f"{label}_end_date {criteria[f'{label}_end_date']!r}."
            )


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

    _reject_unknown_keys(filter_criteria)

    status = filter_criteria.get("status", "uncompleted")
    # isinstance first: set membership on an unhashable value raises TypeError,
    # which escapes as a bare error the model cannot act on.
    if not isinstance(status, str) or status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be 'uncompleted' or 'completed'.")

    tz_info: Optional[ZoneInfo] = _checked_tz(filter_criteria.get("tz"))

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

    _reject_unparsed_dates(filter_criteria, due_filter, completion_filter)

    property_filter = PropertyFilter(
        tag_label=_checked_text(filter_criteria, "tag_label"),
        project_id=_checked_text(filter_criteria, "project_id"),
        priority=_checked_priority(filter_criteria.get("priority")),
        status=status,
        due_date_filter=due_filter,
        completion_date_filter=completion_filter,
    )

    return property_filter, tz_info, _checked_sort_flag(filter_criteria)


def _confirmed_project(client, value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``value`` and confirm the account has that project.

    Returns ``(resolved, None)`` or ``(None, error_json)``, the error carrying
    ``outcome: "project_list_unverifiable"`` when the list could not be read
    and a plain refusal when it could and holds no such project.
    """
    if value is None:
        return None, None

    resolved = resolve_project_id(client, value)
    if is_known_project_id(client, resolved):
        return resolved, None

    # The resolver's own refresh is throttled, so "not known" may just mean
    # the snapshot is stale.
    refreshed = ensure_fresh(client, force=True)
    if refreshed:
        resolved = resolve_project_id(client, resolved)
        if is_known_project_id(client, resolved):
            return resolved, None

    # Only a list that was actually read supports saying a project is absent.
    # A refresh that failed, or one that left nothing readable behind, is not
    # evidence about the account, and reporting it as a miss states something
    # untrue about it. The same predicate guards the protected-task relations.
    if not refreshed or not isinstance(getattr(client, "state", None), dict):
        return None, format_response(
            {
                "outcome": "project_list_unverifiable",
                "status": "error",
                "error": (
                    "Could not read the project list, so this project reference "
                    "could not be confirmed. Retry once the connection recovers."
                ),
            }
        )

    return None, format_response(
        {
            "status": "error",
            "error": (
                f"No project matches {value!r}. List them with ticktick_get_all(search='projects')."
            ),
        }
    )


# --- Public MCP tool ---


@mcp.tool()
@require_ticktick_client
async def ticktick_filter_tasks(filter_criteria: Any, detail: str = DETAIL_COMPACT) -> str:
    """Return the tasks matching every supplied filter criterion.

    Supports any combination of project, priority, tag, status, and a
    date window applied to either the due date (open tasks) or the
    completion timestamp (completed tasks).

    Args:
        detail (str, optional): ``"compact"`` (default) or ``"full"``.
            Compact drops the heavy ``content``/``desc``/checklist
            ``items`` blobs and bulky sync metadata, keeping id,
            projectId, title, dueDate, startDate, priority, status,
            isAllDay, timeZone, tags plus a ``contentPreview`` (first
            ~200 chars of content) so keyword search still works. Full
            returns the raw task objects unchanged. To EDIT a task, fetch
            the full object with ``ticktick_get_by_id`` first, then send
            every field back via ``ticktick_update_task`` -- compact
            output must never feed an update.
        filter_criteria (dict | str): A criteria object, or a JSON string
            that decodes to one. No value below reaches the query
            unchecked, and **any key not on this list is an error** rather
            than something ignored, so a mistyped key fails loudly.
            Recognised keys:

            * ``status``: ``"uncompleted"`` (default) or ``"completed"``.
              When ``"completed"`` you should supply
              ``completion_start_date`` and/or ``completion_end_date``;
              without dates the result is an empty list.
            * ``project_id`` (str): Limit to tasks in this project.
              Accepts the project's name as well as its ID
              (case-insensitive, trimmed). Two projects sharing a
              name is an error, not a guess, and so is a project the
              account does not have.
            * ``priority`` (int): 0=None, 1=Low, 3=Medium, 5=High. Must be
              a JSON integer; the string ``"3"`` and the float ``3.0`` are
              errors, as is any other value.
            * ``tag_label`` (str): Tag name (case-sensitive), non-empty.
              Surrounding whitespace is trimmed.
            * ``due_start_date`` / ``due_end_date`` (str): ISO date or
              datetime strings; only used when ``status='uncompleted'``.
              A value that cannot be read as a date is an error, so a
              window is never silently dropped, and a window that ends
              before it begins is an error too. Bounds apply at day
              granularity, so any time part is ignored rather than
              validated.
            * ``completion_start_date`` / ``completion_end_date`` (str):
              ISO date or datetime strings; only used when
              ``status='completed'``. Same date rules as above.
            * ``tz`` (str): Default IANA timezone applied to date filters,
              e.g. ``"Europe/London"``. A name that does not resolve is an
              error rather than being ignored, so results are never
              labelled with a zone that was not applied.
            * ``sort_by_priority`` (bool): Sort by descending priority.
              Must be a JSON boolean; the strings ``"true"``/``"false"``
              and ``1``/``0`` are errors.

    Returns:
        JSON list of matching task objects (compact by default; see
        ``detail``). Empty list if nothing matches. If a compact result
        would still exceed the size budget, the soonest-due matches are
        returned and a final ``_truncation_note`` element reports how
        many were omitted -- nothing is dropped silently. On invalid
        input or backend failure: ``{"error": "...", "status": "error"}``.
        One error carries an extra key: ``outcome:
        "project_list_unverifiable"`` means the project list could not be
        refreshed to confirm ``project_id``, so retry rather than
        concluding the project is gone.

    Freshness:
        Uncompleted queries read local state, synced from the server at most
        once per throttle window (default 15s,
        ``TICKTICK_MCP_SYNC_TTL_SECONDS``); a change made elsewhere within
        that window may not be visible yet -- call ``ticktick_sync`` to force
        a refresh. Completed queries are always fetched live.

    Limitations:
        - TickTick caps ``get_completed`` at 100 results; very wide
          completion windows are truncated server-side.
        - Filtering happens client-side after the fetch, so additional
          criteria do not reduce the number of network requests.
        - Compact output is for browsing only; full content for one task
          is available via ``ticktick_get_by_id`` or ``detail="full"``.

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
        detail = normalise_detail(detail)
        property_filter, tz_info, sort_by_priority = _build_property_filter(filter_criteria)
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})

    try:
        client = TickTickClientSingleton.get_client()

        resolved_project, refusal = _confirmed_project(client, property_filter.project_id)
        if refusal is not None:
            return refusal
        property_filter.project_id = resolved_project

        # The completed branch fetches live via get_completed, so it needs no
        # sync of its own. Confirming an unknown project above can still force
        # one, on its way to a refusal.
        if property_filter.status != "completed":
            ensure_fresh(client)
        results = await TaskFilterer().filter(
            property_filter=property_filter,
            sort_by_priority=sort_by_priority,
            tz_info=tz_info,
        )
    except ToolLogicError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except ConnectionError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except Exception as exc:
        logger.error("ticktick_filter_tasks: unexpected error: %s", exc, exc_info=True)
        return format_response({"error": f"unexpected error: {exc}", "status": "error"})

    return render_task_list(results, detail=detail)
