"""MCP tools for creating, reading, updating, deleting, and reorganising tasks.

This module sits between the agent-facing MCP layer and ``ticktick-py``'s
``TaskManager``. It addresses three problems the underlying library does
not handle on its own:

* ``builder()`` silently drops date/reminder/priority/timezone fields in
  some situations -- we re-populate the task dict after calling it.
* ``update()`` requires the full task object; any field the caller omits
  is wiped server-side -- we fetch the existing task, then overlay only
  the fields the caller actually set (``exclude_unset=True``).
* The API echoes a sparse response -- we compare it against what we
  sent (``verify_mutation``) and surface ``_verification_warnings``
  alongside the data.

Day-of-week validation is enforced on create/update: callers must supply
``expectedDayOfWeek`` whenever they set ``dueDate``, and a mismatch
raises before any API call.
"""

import datetime
import logging
import os
import re
from typing import Any, List, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_serializer
from ticktick.helpers.time_methods import convert_date_to_tick_tick_format
from tzlocal import get_localzone

from ..client import TickTickClientSingleton
from ..compact import CONTENT_PREVIEW_CHARS, DETAIL_COMPACT, normalise_detail, render_task_list
from ..freshness import ensure_fresh
from ..helpers import (
    ToolLogicError,
    _get_all_tasks_from_ticktick,
    format_response,
    require_ticktick_client,
)
from ..mcp_instance import mcp
from ..projects import resolve_project_id as _resolve_project_id
from ..verification import verify_mutation

logger = logging.getLogger(__name__)


# --- Constants ---

UPDATABLE_FIELDS: set[str] = {
    "id",
    "projectId",
    "title",
    "content",
    "desc",
    "startDate",
    "dueDate",
    "timeZone",
    "isAllDay",
    "allDay",
    "reminders",
    "repeat",
    "repeatFlag",
    "priority",
    "sortOrder",
    "items",
    "status",
    "tags",
}

_DAY_NAMES: list[str] = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
_DAY_NAMES_LOWER: set[str] = {d.lower() for d in _DAY_NAMES}


# --- Models ---


class SubtaskItem(BaseModel):
    """One entry in a task's checklist (``items``)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    title: Optional[str] = None
    status: Optional[int] = None


_EXCLUDE_SENTINEL = object()


class TaskObject(BaseModel):
    """Pydantic model used for ``ticktick_update_task`` input.

    ``populate_by_name=True`` lets callers supply snake_case or camelCase
    keys. ``expectedDayOfWeek`` is validation-only -- it never appears in
    the serialised output sent to the API.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: Optional[str] = None
    projectId: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    desc: Optional[str] = None
    startDate: Optional[Union[str, datetime.datetime]] = None
    dueDate: Optional[Union[str, datetime.datetime]] = None
    timeZone: Optional[str] = None
    isAllDay: Optional[bool] = None
    allDay: Optional[bool] = None
    reminders: Optional[List[Any]] = None
    repeat: Optional[str] = None
    repeatFlag: Optional[str] = None
    priority: Optional[int] = 0
    sortOrder: Optional[int] = None
    items: Optional[List[SubtaskItem]] = None
    status: Optional[int] = None
    tags: Optional[List[str]] = None

    # Validation-only field, stripped on dump.
    expectedDayOfWeek: Optional[str] = None

    @field_serializer("startDate")
    def _serialize_start_date(self, value, _info):
        return _serialize_date_field(value, self.timeZone)

    @field_serializer("dueDate")
    def _serialize_due_date(self, value, _info):
        return _serialize_date_field(value, self.timeZone)

    def model_dump(self, **kwargs):  # type: ignore[override]
        # Always exclude expectedDayOfWeek - it is a validation-only field.
        exclude = kwargs.pop("exclude", None) or set()
        if isinstance(exclude, set):
            exclude = exclude | {"expectedDayOfWeek"}
        elif isinstance(exclude, dict):
            exclude = {**exclude, "expectedDayOfWeek": True}
        else:
            exclude = set(exclude) | {"expectedDayOfWeek"}
        return super().model_dump(exclude=exclude, **kwargs)

    def update(self, src: dict) -> None:
        """Overlay non-None values from ``src`` onto this model's fields."""
        for key, value in src.items():
            if value is None:
                continue
            if key in self.model_fields:
                object.__setattr__(self, key, value)


# TickTick parses these in a title rather than showing them: '#tag' creates the
# tag on the account, '@' assigns, '~' sets a duration. Each binds to what
# follows it, so a bare '#' or a trailing '@' is ordinary prose and must not warn.
TITLE_MARKERS = (
    (re.compile(r"#\w"), "#", "creates a tag on your account"),
    (re.compile(r"@\w"), "@", "is read as an assignee"),
    (re.compile(r"~\d"), "~", "is read as a duration"),
)


def _title_marker_warning(title: str) -> Optional[str]:
    found = [(ch, effect) for rx, ch, effect in TITLE_MARKERS if rx.search(title or "")]
    if not found:
        return None
    parts = ", ".join(f"{ch!r} {effect}" for ch, effect in found)
    return (
        f"Title contains characters parsed by TickTick as markers rather than text: "
        f"{parts}. The title will not read back as written."
    )


PROTECTED_TASK_IDS_ENV = "TICKTICK_MCP_PROTECTED_TASK_IDS"


def _norm_task_id(value: Any) -> str:
    """Normalise an id for comparison.

    Config and caller ids must go through the same funnel: normalising only the
    configured side lets a padded or recased id slip past the check and reach
    the API, which resolves it anyway.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().strip("\"'").casefold()


def _load_protected_task_ids() -> frozenset:
    raw = os.environ.get(PROTECTED_TASK_IDS_ENV, "")
    ids = frozenset(_norm_task_id(part) for part in raw.replace(",", " ").split())
    return ids - {""}


PROTECTED_TASK_IDS: frozenset = _load_protected_task_ids()

if PROTECTED_TASK_IDS:
    # Count only - the ids themselves are account data.
    logger.info("Protected task ids loaded: %d", len(PROTECTED_TASK_IDS))


def _protected_refusal(ids) -> Optional[str]:
    """Return a refusal payload if any of ``ids`` names a protected task.

    Runs before anything that reads or writes the task. A batch containing one
    protected id is refused whole, because a partial delete cannot be undone.
    """
    if not PROTECTED_TASK_IDS:
        return None
    hits = sorted({_norm_task_id(i) for i in ids} & PROTECTED_TASK_IDS)
    if not hits:
        return None
    return _refusal_payload(hits, "named directly")


def _protected_relation_refusal(client, ids, already_fresh: bool = False) -> Optional[str]:
    """Refuse when ``ids`` are unprotected but a protected task hangs off them.

    TickTick propagates delete/move through subtasks and lets a reparent
    restructure a task nobody named, so an id check alone leaves a protected
    task reachable via its parent.

    Forces a refresh itself, and refuses if that fails. The parent/child links
    come out of local state, so on a snapshot it could not update the guard
    cannot rule out a subtask attached elsewhere, and the delete it would
    otherwise allow is not reversible. Owning the refresh here means no caller
    has to place it correctly. Costs nothing when no task is protected: the
    check below short-circuits first.
    """
    if not PROTECTED_TASK_IDS or client is None:
        return None
    # A caller that has just forced its own refresh passes already_fresh, so
    # protected mode does not cost two full-account syncs per call.
    if not (already_fresh or ensure_fresh(client, force=True)):
        # ensure_fresh is fail-soft, so a failure leaves state at whatever it
        # was - arbitrarily old on a long-lived server. Refuse rather than
        # decide on it: a refusal is recoverable, the delete it would allow
        # is not.
        return format_response(
            {
                "outcome": "protection_unverifiable",
                "error": (
                    "Refused: local state could not be refreshed, so a protected task "
                    "hanging off this one could not be ruled out. Retry once the "
                    "connection recovers."
                ),
            }
        )
    # TickTick propagates a delete through the whole subtree, so checking only
    # the named task's own childIds would miss a protected grandchild. Walk the
    # descendants, using a reverse index over local state as well as childIds:
    # a task can record its parentId without the parent listing it.
    children: dict = {}
    for task in getattr(client, "state", {}).get("tasks") or []:
        if isinstance(task, dict) and task.get("parentId") and task.get("id"):
            children.setdefault(_norm_task_id(task["parentId"]), set()).add(task["id"])

    hits = set()
    for task_id in ids:
        if not isinstance(task_id, str):
            continue
        seen: set = set()
        frontier = [task_id]
        while frontier:
            current = frontier.pop()
            key = _norm_task_id(current)
            if key in seen:
                continue
            seen.add(key)
            try:
                task = client.get_by_id(current)
            except Exception:  # a lookup failure must not open the guard
                task = None
            descendants = set(children.get(key, ()))
            if isinstance(task, dict):
                descendants |= {c for c in (task.get("childIds") or []) if isinstance(c, str)}
                # The named task's own parent is restructured by a reparent,
                # but a descendant's parent is inside the subtree already.
                if current == task_id and task.get("parentId"):
                    hits |= {_norm_task_id(task["parentId"])} & PROTECTED_TASK_IDS
            hits |= {_norm_task_id(d) for d in descendants} & PROTECTED_TASK_IDS
            frontier.extend(d for d in descendants if _norm_task_id(d) not in seen)
    if not hits:
        return None
    return _refusal_payload(sorted(hits), "a parent or descendant of the task you named")


def _refusal_payload(hits: list, relation: str) -> str:
    return format_response(
        {
            "outcome": "protected_task",
            "protected_task_ids": hits,
            "error": (
                f"Refused: {', '.join(hits)} is {relation} and is listed in "
                f"{PROTECTED_TASK_IDS_ENV}, which marks tasks that must never be "
                "modified. Nothing was changed. Remove the id from "
                f"{PROTECTED_TASK_IDS_ENV} if this was intended."
            ),
        }
    )


def _serialize_date_field(value: Any, tz: Optional[str]) -> Any:
    """Serialise a date field for the API."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime.datetime):
        tz_name = tz or _local_tz_name()
        return convert_date_to_tick_tick_format(value, tz_name)
    return value


def _local_tz_name() -> str:
    """Return the system's IANA timezone name."""
    local = get_localzone()
    return getattr(local, "key", None) or str(local)


# --- Day-of-week validation ---


def _validate_day_of_week(
    date_value: Any,
    expected_day: str,
    field_name: str,
    time_zone: Optional[str] = None,
) -> None:
    """Raise ``ToolLogicError`` if ``date_value`` does not fall on ``expected_day``.

    ``expected_day`` must be an English weekday name (Monday..Sunday).
    Non-English names are rejected with a clear error message.

    If ``date_value`` is ``None`` we no-op.
    """
    if date_value is None:
        return

    if not isinstance(expected_day, str):
        raise ToolLogicError(f"Invalid expectedDayOfWeek for {field_name}: must be a string")

    normalised = expected_day.strip()
    if normalised.lower() not in _DAY_NAMES_LOWER:
        raise ToolLogicError(
            f"Invalid expectedDayOfWeek for {field_name}: {expected_day!r}. "
            "Must be an English weekday name (Monday..Sunday)."
        )

    if isinstance(date_value, datetime.datetime):
        dt = date_value
    elif isinstance(date_value, str):
        cleaned = date_value.replace("Z", "+00:00") if date_value.endswith("Z") else date_value
        try:
            dt = datetime.datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise ToolLogicError(f"Cannot parse {field_name} for day-of-week check: {exc}") from exc
    else:
        raise ToolLogicError(
            f"Cannot interpret {field_name} value of type {type(date_value).__name__}"
        )

    tz_obj: Optional[ZoneInfo] = None
    if time_zone:
        try:
            tz_obj = ZoneInfo(time_zone)
        except ZoneInfoNotFoundError:
            tz_obj = None

    if tz_obj is not None and dt.tzinfo is not None:
        dt = dt.astimezone(tz_obj)

    actual = _DAY_NAMES[dt.weekday()]
    expected_canonical = next(d for d in _DAY_NAMES if d.lower() == normalised.lower())

    if actual.lower() != normalised.lower():
        tz_clause = f" in {time_zone}" if tz_obj is not None else ""
        raise ToolLogicError(
            f"{field_name} falls on {actual}{tz_clause}, not {expected_canonical}."
        )


def _parse_iso_to_datetime(value: str, field_name: str) -> datetime.datetime:
    """Parse an ISO datetime string, raising ValueError on bad input."""
    cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid date format for {field_name}: {value!r}") from exc


def _normalise_reminder(reminder: Any) -> Optional[str]:
    """Reduce a reminder of either form to its trigger string."""
    if isinstance(reminder, str):
        return reminder
    if isinstance(reminder, dict):
        return reminder.get("trigger")
    return None


def _is_recurring(task: dict) -> bool:
    """True if ``task`` carries recurrence metadata.

    Keyed on the PRESENCE of any recurrence field, not on ``repeatFlag``
    alone: a live recurring task can have an empty ``repeatFlag`` while still
    carrying ``repeatFrom`` / ``repeatTaskId`` / ``repeatFirstDate``.
    ``repeatFrom`` is a mode field (``"0"`` = from due date, ``"2"`` = from
    completion date), so its mere presence signals recurrence.
    """
    if not isinstance(task, dict):
        return False
    if task.get("repeatTaskId") or task.get("repeatFirstDate"):
        return True
    if task.get("repeatFrom") not in (None, "", "0"):
        return True
    repeat_flag = task.get("repeatFlag")
    return bool(repeat_flag and str(repeat_flag).strip())


# --- create_task ---


@mcp.tool()
@require_ticktick_client
async def ticktick_create_task(
    title: str,
    project_id: Optional[str] = None,
    content: Optional[str] = None,
    desc: Optional[str] = None,
    all_day: Optional[bool] = None,
    start_date: Optional[str] = None,
    due_date: Optional[str] = None,
    expected_day_of_week: Optional[str] = None,
    time_zone: Optional[str] = None,
    reminders: Optional[List[str]] = None,
    repeat: Optional[str] = None,
    priority: Optional[int] = None,
    sort_order: Optional[int] = None,
    items: Optional[List[dict]] = None,
) -> str:
    """Create a new task.

    Args:
        title (str): Task title. Required.
        project_id (str, optional): Project ID or name. Defaults to inbox.
            Accepts the project's name as well as its ID
            (case-insensitive, trimmed; "Inbox" resolves to the inbox).
            Two projects sharing a name is an error, not a guess.
        content (str, optional): Long-form content (markdown supported).
        desc (str, optional): Short description / checklist subtitle.
        all_day (bool, optional): True for all-day tasks.
        start_date (str, optional): ISO 8601 start datetime, e.g.
            ``"2026-04-13T09:00:00+01:00"``.
        due_date (str, optional): ISO 8601 due datetime.
        expected_day_of_week (str, optional): English weekday name. Required
            when ``due_date`` is set; mismatch returns an error.
        time_zone (str, optional): IANA timezone. Defaults to the system
            timezone for date conversion.
        reminders (list[str], optional): TickTick trigger strings, e.g.
            ``["TRIGGER:-PT30M"]``.
        repeat (str, optional): Recurrence rule (RFC 5545 RRULE).
        priority (int, optional): 0=None, 1=Low, 3=Medium, 5=High.
        sort_order (int, optional): Position within project.
        items (list[dict], optional): Subtask items.

    Returns:
        JSON object containing the created task. If verification flags
        an issue, ``_verification_warnings`` is attached. Without
        ``due_date`` a warning is added because TickTick will not trigger
        a reminder.
        On failure: ``{"error": "...", "status": "error"}``.

    Limitations:
        - ``builder()`` in ``ticktick-py`` sometimes omits dates,
          reminders, priority and timezone; we re-populate them after
          the call.

    Agent Usage Guide:
        - Always pair ``due_date`` with ``expected_day_of_week``.
        - Pass a project name directly, or list ids with
          ticktick_get_all(search="projects").

    Example:
        ticktick_create_task(
            title="Replace kitchen tap washer",
            project_id="<your-project-id>",
            due_date="2026-06-01T20:45:00+01:00",
            expected_day_of_week="Monday",
            time_zone="Europe/London",
            priority=3,
        )
    """
    client = TickTickClientSingleton.get_client()

    try:
        project_id = _resolve_project_id(client, project_id)
        # --- Date parsing first, so bad input surfaces with a clear error. ---
        tz_for_dates = time_zone or _local_tz_name()
        start_dt: Optional[datetime.datetime] = None
        due_dt: Optional[datetime.datetime] = None
        if start_date is not None:
            start_dt = _parse_iso_to_datetime(start_date, "start_date")
        if due_date is not None:
            due_dt = _parse_iso_to_datetime(due_date, "due_date")

        # --- Day-of-week validation (before any network I/O) ---
        if due_date is not None:
            if expected_day_of_week is None:
                return format_response(
                    {
                        "error": (
                            "due_date set but expected_day_of_week is missing. "
                            "Supply the English day name to confirm the date."
                        ),
                        "status": "error",
                    }
                )
            _validate_day_of_week(due_date, expected_day_of_week, "due_date", time_zone)

        # --- Build task dict via the library. ticktick-py expects the
        # TickTick API field names (camelCase), so translate here. ---
        builder_kwargs: dict[str, Any] = {"title": title}
        if project_id is not None:
            builder_kwargs["projectId"] = project_id
        if content is not None:
            builder_kwargs["content"] = content
        if desc is not None:
            builder_kwargs["desc"] = desc
        if all_day is not None:
            builder_kwargs["allDay"] = all_day
        if start_dt is not None:
            builder_kwargs["startDate"] = start_dt
        if due_dt is not None:
            builder_kwargs["dueDate"] = due_dt
        if time_zone is not None:
            builder_kwargs["timeZone"] = time_zone
        if reminders is not None:
            builder_kwargs["reminders"] = reminders
        if repeat is not None:
            builder_kwargs["repeat"] = repeat
        if priority is not None:
            builder_kwargs["priority"] = priority
        if sort_order is not None:
            builder_kwargs["sortOrder"] = sort_order
        if items is not None:
            builder_kwargs["items"] = items

        task_dict = client.task.builder(**builder_kwargs)

        # --- Re-populate fields the builder may have dropped ---
        if start_dt is not None and not task_dict.get("startDate"):
            task_dict["startDate"] = convert_date_to_tick_tick_format(start_dt, tz_for_dates)
        if due_dt is not None and not task_dict.get("dueDate"):
            task_dict["dueDate"] = convert_date_to_tick_tick_format(due_dt, tz_for_dates)
        if reminders is not None and "reminders" not in task_dict:
            task_dict["reminders"] = reminders
        if priority is not None and "priority" not in task_dict:
            task_dict["priority"] = priority
        if time_zone is not None and "timeZone" not in task_dict:
            task_dict["timeZone"] = time_zone

        created = client.task.create(task_dict)

        warnings = list(verify_mutation("create", task_dict, created or {}))
        # Read all-day off what was built and created, not off the request:
        # ticktick-py infers it when start and due are both exact midnight, so
        # a caller who never passed all_day can still get an all-day task, and
        # that is the case they cannot already know about.
        landed_all_day = bool(
            all_day
            or task_dict.get("allDay")
            or task_dict.get("isAllDay")
            or (isinstance(created, dict) and (created.get("isAllDay") or created.get("allDay")))
        )
        if due_date is None:
            warnings.append("No due_date set: TickTick will not trigger reminders for this task.")
        elif landed_all_day:
            warnings.append(
                "Task is all-day: TickTick will not trigger a timed reminder for it. "
                "Set a due_date with a time instead if you want one."
            )

        marker_warning = _title_marker_warning(title)
        if marker_warning:
            warnings.append(marker_warning)

        if content and len(content) > CONTENT_PREVIEW_CHARS:
            warnings.append(
                f"Content is longer than the {CONTENT_PREVIEW_CHARS}-char compact preview. "
                "List tools return compact output by default, so the remainder will not "
                "appear there or match a keyword search against it - keep the searchable "
                "part first, or park the detail elsewhere and reference it."
            )

        result = dict(created) if isinstance(created, dict) else {"result": created}
        if warnings:
            result["_verification_warnings"] = warnings

        return format_response(result)

    except ToolLogicError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except Exception as exc:
        logger.error("ticktick_create_task failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- update_task ---


@mcp.tool(name="ticktick_update_task")
@require_ticktick_client
async def update_task(task_object: TaskObject) -> str:
    """Update an existing task without wiping unmodified fields.

    The TickTick API requires the entire editable task on every update;
    any omitted field is wiped server-side. To prevent that we fetch the
    current task, then overlay ONLY the fields the caller explicitly
    set (``exclude_unset=True``).

    Args:
        task_object (TaskObject): Must include ``id``. All other fields
            are optional; set only the ones you want to change. When
            ``dueDate`` is set you must also set ``expectedDayOfWeek``.

    Returns:
        JSON object containing the updated task.
        ``_verification_warnings`` is attached if the response did not
        match what we sent.
        On failure: ``{"error": "...", "status": "error"}``.

    Limitations:
        - Read-only API fields (``creator``, ``etag``, ``createdTime``,
          ``modifiedTime``, ``deleted``, ``kind``, ``isFloating``) are
          stripped before the call.

    Agent Usage Guide:
        - To reschedule a task, send a single update with the new
          ``dueDate`` + ``expectedDayOfWeek``. Do NOT complete and
          recreate.

    Example:
        ticktick_update_task(task_object={
            "id": "60ca9dbc8f08516d9dd56324",
            "projectId": "<your-project-id>",
            "priority": 5,
            "dueDate": "2026-06-15T20:45:00+01:00",
            "expectedDayOfWeek": "Monday",
            "timeZone": "Europe/London",
        })
    """
    # Callers may pass a raw dict; the guard runs before that is normalised
    # below, so read the id from either shape rather than bypassing on dicts.
    refusal = _protected_refusal(
        (task_object.get("id") if isinstance(task_object, dict) else task_object.id,)
    )
    if refusal is not None:
        return refusal

    try:
        if isinstance(task_object, dict):
            task_object = TaskObject(**task_object)

        if not task_object.id:
            return format_response({"error": "task_object.id is required", "status": "error"})

        client = TickTickClientSingleton.get_client()

        # Day-of-week validation before any I/O.
        if "dueDate" in task_object.model_fields_set and task_object.dueDate is not None:
            if not task_object.expectedDayOfWeek:
                return format_response(
                    {
                        "error": (
                            "dueDate set but expectedDayOfWeek is missing. "
                            "Supply the English day name to confirm the date."
                        ),
                        "status": "error",
                    }
                )
            _validate_day_of_week(
                task_object.dueDate,
                task_object.expectedDayOfWeek,
                "dueDate",
                task_object.timeZone,
            )

        # Sync before reading the task we are about to overlay-and-POST: an
        # update built on a stale snapshot can re-POST stale fields.
        ensure_fresh(client, force=True)

        if task_object.projectId:
            task_object.projectId = _resolve_project_id(client, task_object.projectId)

        existing = client.get_by_id(task_object.id)
        if not isinstance(existing, dict):
            return format_response(
                {"error": f"Task not found: {task_object.id}", "status": "error"}
            )

        # get_by_id returns {} (an empty dict -- still a dict, so it slips past
        # the guard above) for any id not in local sync state: typically a
        # completed recurring-history occurrence (its status-2 record is never
        # synced locally) or an unknown id. With no task to copy, the merged body
        # has no routable projectId, and the open-API update then silently no-ops
        # (returns "") -- which used to surface as a dead-end "re-read and retry"
        # that can never succeed. Name the one action that works instead: supply a
        # projectId. When the caller already set one, fall through -- a routable
        # body reopens the occurrence cleanly.
        if not existing and not (
            "projectId" in task_object.model_fields_set and task_object.projectId
        ):
            return format_response(
                {
                    "outcome": "needs_project_id",
                    "status": "error",
                    "error": (
                        f"{task_object.id} is not in local sync state (typically a "
                        "completed recurring-history occurrence, or an unknown id), so "
                        "the update cannot be routed. Re-call ticktick_update_task with "
                        "projectId set on the task object to reopen/update it -- the id "
                        "alone is not enough."
                    ),
                }
            )

        # Reopening a completed recurring occurrence by its SERIES id is a silent
        # false-success: completing a recurring task rolls the SAME id forward
        # (status back to 0, dueDate advanced) and writes the completed instance to
        # a SEPARATE history record under a new id. So the series id is already
        # status 0 -- a status:0 "reopen" changes nothing and does NOT undo the
        # completion, yet the API echoes a normal task that reads as success.
        # Refuse with an explanation when the caller's ONLY substantive intent is
        # status:0 on an already-open recurring task. Any real edit (some field
        # other than status is set) falls through untouched, so the standard
        # fetch-full-object-then-resend reschedule workflow is unaffected.
        substantive_fields = (task_object.model_fields_set & UPDATABLE_FIELDS) - {
            "id",
            "projectId",
            "status",
        }
        if (
            "status" in task_object.model_fields_set
            and task_object.status == 0
            and not substantive_fields
            and _is_recurring(existing)
            and existing.get("status", 0) == 0
        ):
            return format_response(
                {
                    "outcome": "reopen_no_effect",
                    "status": "error",
                    "error": (
                        "this is a recurring task already rolled forward to its next "
                        "occurrence (status 0), so a status:0 update changes nothing and "
                        "does NOT undo the prior completion -- the completed instance is a "
                        "separate history record under a different id. To revert the "
                        "completion, delete that history record and reset the dueDate; to "
                        "advance or skip the series, update dueDate instead."
                    ),
                }
            )

        merged: dict = {k: v for k, v in existing.items() if k in UPDATABLE_FIELDS}

        explicit = task_object.model_dump(mode="json", exclude_unset=True)
        for key, value in explicit.items():
            if key in UPDATABLE_FIELDS:
                merged[key] = value

        # Normalise reminders to list of trigger strings.
        if "reminders" in merged and isinstance(merged["reminders"], list):
            merged["reminders"] = [_normalise_reminder(r) for r in merged["reminders"]]
            merged["reminders"] = [r for r in merged["reminders"] if r is not None]

        updated = client.task.update(merged)

        # An empty / non-dict API response means the server echoed nothing.
        # That is how the open-API update can silently no-op (e.g. trying to
        # reopen a completed recurring occurrence via status:0). Re-read to
        # find out whether the change actually took and tell the caller what
        # to do, instead of returning {"result": ""} that reads as success.
        if not isinstance(updated, dict) or not updated:
            recheck = client.get_by_id(task_object.id)
            # Compare only robust exact fields (skip content/dates, whose
            # server-side normalisation would cause false negatives).
            sent = {k: v for k, v in explicit.items() if k in {"status", "priority", "title"}}
            applied = (
                isinstance(recheck, dict)
                and bool(recheck)
                and all(recheck.get(k) == v for k, v in sent.items())
            )
            if applied:
                result = dict(recheck)
                result["outcome"] = "updated"
                return format_response(result)
            result = dict(recheck) if isinstance(recheck, dict) and recheck else {}
            result["outcome"] = "no_op"
            result["error"] = (
                "the update returned an empty response and a re-read shows it "
                "did not apply. Re-read with ticktick_get_by_id to confirm the "
                "current state before retrying."
            )
            return format_response(result)

        warnings = verify_mutation("update", merged, updated)
        result = dict(updated)
        if warnings:
            result["_verification_warnings"] = warnings

        return format_response(result)

    except ToolLogicError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except Exception as exc:
        logger.error("ticktick_update_task failed: %s", exc, exc_info=True)
        return format_response(
            {
                "error": f"Failed to update task {getattr(task_object, 'id', '<unknown>')}: {exc}",
                "status": "error",
            }
        )


# --- delete_tasks ---


@mcp.tool()
@require_ticktick_client
async def ticktick_delete_tasks(
    task_ids: Union[str, List[str]],
    project_id: Optional[str] = None,
) -> str:
    """Delete one or more tasks.

    Args:
        task_ids (str | list[str]): A single task ID, or a list of IDs.
            An empty list returns an error.
        project_id (str, optional): Used to construct a minimal delete
            payload when ``get_by_id`` cannot find the task locally
            (typical for completed tasks).

    Returns:
        ``{"status": "success", "deleted_count": N, "tasks_deleted_ids":
        [...]}`` on success. Tasks that could not be matched at all are
        returned as ``status="not_found"`` with ``missing_ids`` /
        ``invalid_ids`` arrays. Partial success surfaces ``warnings``.
        Empty input: ``{"status": "error", "message": "No task IDs..."}``.

    Agent Usage Guide:
        - For tasks already completed in TickTick, supply ``project_id``
          -- ``get_by_id`` does not see completed tasks.

    Example:
        ticktick_delete_tasks(
            task_ids=["abc123", "def456"],
            project_id="<your-project-id>",
        )
    """
    ids = [task_ids] if isinstance(task_ids, str) else list(task_ids or [])
    refusal = _protected_refusal(ids)
    if refusal is not None:
        return refusal

    try:
        client = TickTickClientSingleton.get_client()

        # Forced first, so the guard, the resolver and the per-id pre-read all
        # work off one refresh. A task missing from a stale snapshot falls to
        # the project_id branch, which deletes using the caller's project
        # rather than the task's own and still reports success.
        fresh = ensure_fresh(client, force=True)

        relation_refusal = _protected_relation_refusal(client, ids, already_fresh=fresh)
        if relation_refusal is not None:
            return relation_refusal

        project_id = _resolve_project_id(client, project_id)

        input_was_string = isinstance(task_ids, str)

        if not ids:
            return format_response({"status": "error", "message": "No task IDs provided."})

        tasks_to_delete: list = []
        deleted_ids: list[str] = []
        missing_ids: list[str] = []
        invalid_ids: list[str] = []

        for tid in ids:
            if not isinstance(tid, str) or not tid:
                invalid_ids.append(str(tid))
                continue

            task_obj = client.get_by_id(tid)

            if isinstance(task_obj, dict) and task_obj:
                if "projectId" in task_obj and "title" in task_obj:
                    tasks_to_delete.append(task_obj)
                    deleted_ids.append(tid)
                else:
                    invalid_ids.append(tid)
            elif project_id:
                tasks_to_delete.append({"id": tid, "projectId": project_id})
                deleted_ids.append(tid)
            else:
                missing_ids.append(tid)

        if not tasks_to_delete:
            response: dict[str, Any] = {"status": "not_found"}
            if missing_ids:
                response["missing_ids"] = missing_ids
            if invalid_ids:
                response["invalid_ids"] = invalid_ids
            return format_response(response)

        payload = tasks_to_delete[0] if input_was_string else tasks_to_delete
        api_response = client.task.delete(payload)

        result: dict[str, Any] = {
            "status": "success",
            "deleted_count": len(deleted_ids),
            "tasks_deleted_ids": deleted_ids,
            "api_response": api_response,
        }
        if missing_ids or invalid_ids:
            warning_parts = []
            if missing_ids:
                warning_parts.append(f"Missing IDs not deleted: {', '.join(missing_ids)}")
            if invalid_ids:
                warning_parts.append(f"Invalid IDs skipped: {', '.join(invalid_ids)}")
            result["warnings"] = "; ".join(warning_parts)
        return format_response(result)

    except ConnectionError as exc:
        return format_response({"error": str(exc), "status": "error"})
    except Exception as exc:
        logger.error("ticktick_delete_tasks failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- get_tasks_from_project ---


@mcp.tool()
@require_ticktick_client
async def ticktick_get_tasks_from_project(project_id: str, detail: str = DETAIL_COMPACT) -> str:
    """Return every open task in a project.

    Args:
        project_id (str): The project's ID or name. Accepts the project's name as well as its ID
            (case-insensitive, trimmed; "Inbox" resolves to the inbox).
            Two projects sharing a name is an error, not a guess.
            List them with ``ticktick_get_all(search="projects")``.
        detail (str, optional): ``"compact"`` (default) or ``"full"``.
            Compact drops the heavy ``content``/``desc``/checklist
            ``items`` blobs and bulky sync metadata, keeping id,
            projectId, title, dueDate, startDate, priority, status,
            isAllDay, timeZone, tags plus a ``contentPreview`` (first
            ~200 chars of content) so keyword search still works. Full
            returns the raw task objects unchanged.

    Returns:
        Compact (default): JSON list of compact task objects. If the
        compact payload would still exceed the size budget, the
        soonest-due tasks are returned and a final ``_truncation_note``
        element reports how many were omitted -- nothing is dropped
        silently.
        Full: JSON list of raw task objects (empty list if none).
        On failure: ``{"error": "...", "status": "error"}``.

    Limitations:
        - Completed tasks are NOT included.
        - Compact output is for browsing only. To EDIT a task, fetch the
          full object with ``ticktick_get_by_id`` first, then send every
          field back via ``ticktick_update_task`` (the API wipes any
          field omitted from an update). Get the full content of a single
          task with ``ticktick_get_by_id``, or pass ``detail="full"``.

    Freshness:
        Local state is synced from the server at most once per throttle
        window (default 15s, ``TICKTICK_MCP_SYNC_TTL_SECONDS``); an edit made
        elsewhere within that window may not be visible yet. Call
        ``ticktick_sync`` to force an immediate refresh.

    Example:
        ticktick_get_tasks_from_project(
            project_id="<your-project-id>"
        )
    """
    try:
        detail = normalise_detail(detail)
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})

    try:
        client = TickTickClientSingleton.get_client()

        ensure_fresh(client)
        project_id = _resolve_project_id(client, project_id)
        tasks = client.task.get_from_project(project_id)
        if tasks is None:
            tasks = []
        elif isinstance(tasks, dict):
            tasks = [tasks]
        return render_task_list(list(tasks), detail=detail)
    except Exception as exc:
        logger.error("ticktick_get_tasks_from_project failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- complete_task ---


@mcp.tool()
@require_ticktick_client
async def ticktick_complete_task(task_id: str) -> str:
    """Mark a task as completed.

    Args:
        task_id (str): The task's full ID.

    Returns:
        JSON object containing the refetched task (status=2 on success).
        ``_verification_warnings`` is attached if the refetch shows the
        task is still open.
        Missing task: ``{"status": "not_found", "error": "..."}``.
        Other failures: ``{"error": "...", "status": "error"}``.

    Limitations:
        - Once completed, the ``content`` field becomes immutable.
          Update content with resolution notes BEFORE calling this tool.

    Example:
        ticktick_complete_task(task_id="60ca9dbc8f08516d9dd56324")
    """
    refusal = _protected_refusal((task_id,))
    if refusal is not None:
        return refusal

    try:
        client = TickTickClientSingleton.get_client()

        # Sync before reading the body we are about to POST to /complete.
        ensure_fresh(client, force=True)
        task_obj = client.get_by_id(task_id)
        if not isinstance(task_obj, dict) or not task_obj or "projectId" not in task_obj:
            return format_response(
                {
                    "status": "not_found",
                    "error": f"Task not found: {task_id}",
                }
            )

        recurring = _is_recurring(task_obj)
        client.task.complete(task_obj)

        refetched = client.get_by_id(task_id)

        # A recurring task rolls forward on completion: the SAME id reappears
        # as the next occurrence (status 0, due date advanced). That is a
        # successful completion, not a failure -- report it as such instead
        # of the misleading "status still indicates open" warning the
        # non-recurring path would emit.
        rolled_forward = (
            recurring
            and isinstance(refetched, dict)
            and refetched
            and refetched.get("status", 0) != 2
        )
        if rolled_forward:
            result = dict(refetched)
            result["outcome"] = "completed_recurring"
            result["next_occurrence_id"] = refetched.get("id")
            return format_response(result)

        if not isinstance(refetched, dict) or not refetched:
            # Task left the active list (completed in place). For both
            # non-recurring tasks and recurring tasks with no live rule this
            # is the normal success signal.
            return format_response(
                {
                    "outcome": "completed",
                    "_verification_warnings": [
                        "post-complete verification failed: task could not be re-fetched"
                    ],
                }
            )

        result = dict(refetched)
        if refetched.get("status", 0) == 2:
            result["outcome"] = "completed"
        else:
            # Not recurring, yet still open. Something did not take, and
            # calling it "completed" would assert a success we cannot back.
            result["outcome"] = "uncertain"
            result["_verification_warnings"] = [
                "post-complete verification failed: status still indicates open"
            ]
        return format_response(result)

    except Exception as exc:
        logger.error("ticktick_complete_task failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- move_task ---


@mcp.tool()
@require_ticktick_client
async def ticktick_move_task(task_id: str, new_project_id: str) -> str:
    """Move a task into a different project.

    Args:
        task_id (str): The task's full ID.
        new_project_id (str): Destination project's ID or name.
            Accepts the project's name as well as its ID
            (case-insensitive, trimmed; "Inbox" resolves to the inbox).
            Two projects sharing a name is an error, not a guess.

    Returns:
        JSON object containing the moved task. If the target project
        cannot be looked up locally, the move is still attempted.
        Missing source task (no projectId field):
        ``{"status": "not_found", ...}``.
        Other failures: ``{"error": "...", "status": "error"}``.

    Example:
        ticktick_move_task(
            task_id="60ca9dbc8f08516d9dd56324",
            new_project_id="<your-project-id>",
        )
    """
    refusal = _protected_refusal((task_id,))
    if refusal is not None:
        return refusal

    try:
        client = TickTickClientSingleton.get_client()

        # Forced first, so the guard, the resolver and the pre-read all work
        # off one refresh. task.move() takes fromProjectId out of the body
        # fetched here, so a snapshot inside the throttle window would move the
        # task out of the wrong project.
        fresh = ensure_fresh(client, force=True)

        relation_refusal = _protected_relation_refusal(client, (task_id,), already_fresh=fresh)
        if relation_refusal is not None:
            return relation_refusal

        new_project_id = _resolve_project_id(client, new_project_id)
        task_obj = client.get_by_id(task_id)
        if isinstance(task_obj, dict) and task_obj and "projectId" not in task_obj:
            return format_response(
                {
                    "status": "not_found",
                    "error": f"Task {task_id} found but has no projectId.",
                }
            )

        # If get_by_id returned None we let the next line raise; the
        # outer except wraps it as a generic error -- the documented
        # behaviour the test suite pins.
        _project_id_source = task_obj["projectId"]  # noqa: F841

        # Best-effort target project lookup; warn but continue if missing.
        try:
            client.get_by_id(new_project_id)
        except Exception as exc:
            logger.debug("Target project lookup failed: %s", exc)

        moved = client.task.move(task_obj, new_project_id)
        return format_response(moved if isinstance(moved, dict) else {"result": moved})

    except Exception as exc:
        logger.error("ticktick_move_task failed: %s", exc, exc_info=True)
        return format_response(
            {
                "error": f"Failed to move task: {exc}",
                "status": "error",
            }
        )


# --- make_subtask ---


@mcp.tool()
@require_ticktick_client
async def ticktick_make_subtask(parent_task_id: str, child_task_id: str) -> str:
    """Nest ``child_task_id`` under ``parent_task_id``.

    Args:
        parent_task_id (str): The parent task's ID.
        child_task_id (str): The task to become a subtask. Must differ
            from ``parent_task_id`` and live in the same project.

    Returns:
        On success: ``{"status": "success", "updated_parent_task": ...,
        "api_response": ...}``.
        Missing child / parent: ``{"status": "not_found", "error": "..."}``.
        Cross-project: ``{"error": "...same project...",
        "child_project": "...", "parent_project": "..."}``.

    Example:
        ticktick_make_subtask(
            parent_task_id="60ca9dbc8f08516d9dd56324",
            child_task_id="60ca9dbc8f08516d9dd56325",
        )
    """
    refusal = _protected_refusal((parent_task_id, child_task_id))
    if refusal is not None:
        return refusal

    try:
        if not isinstance(parent_task_id, str) or not parent_task_id:
            return format_response(
                {"error": "parent_task_id must be a non-empty string", "status": "error"}
            )
        if not isinstance(child_task_id, str) or not child_task_id:
            return format_response(
                {"error": "child_task_id must be a non-empty string", "status": "error"}
            )
        if parent_task_id == child_task_id:
            return format_response(
                {
                    "error": "parent_task_id and child_task_id cannot be the same",
                    "status": "error",
                }
            )

        client = TickTickClientSingleton.get_client()

        # Forced first, so the guard and both pre-reads work off one refresh.
        # Both ends are read from local state and sent back.
        fresh = ensure_fresh(client, force=True)

        relation_refusal = _protected_relation_refusal(
            client, (parent_task_id, child_task_id), already_fresh=fresh
        )
        if relation_refusal is not None:
            return relation_refusal

        child = client.get_by_id(child_task_id)
        if not isinstance(child, dict) or not child:
            return format_response(
                {
                    "status": "not_found",
                    "error": f"Child task not found: {child_task_id}",
                }
            )

        parent = client.get_by_id(parent_task_id)
        if not isinstance(parent, dict) or not parent:
            return format_response(
                {
                    "status": "not_found",
                    "error": f"Parent task not found: {parent_task_id}",
                }
            )

        child_project = child.get("projectId")
        parent_project = parent.get("projectId")
        if child_project != parent_project:
            return format_response(
                {
                    "error": "Parent and child must be in the same project.",
                    "child_project": child_project,
                    "parent_project": parent_project,
                }
            )

        api_response = client.task.make_subtask(child, parent_task_id)
        updated_parent = client.get_by_id(parent_task_id)

        return format_response(
            {
                "status": "success",
                "updated_parent_task": updated_parent
                if isinstance(updated_parent, dict)
                else parent,
                "api_response": api_response,
            }
        )

    except Exception as exc:
        logger.error("ticktick_make_subtask failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- get_by_id ---


@mcp.tool()
@require_ticktick_client
async def ticktick_get_by_id(obj_id: str) -> str:
    """Look up any object (task, project, tag) by its ID.

    Args:
        obj_id (str): The object's full ID.

    Returns:
        JSON object of the matching record, or ``null`` if not found.
        On failure: ``{"error": "...", "status": "error"}``.

    Freshness:
        Local state is synced from the server at most once per throttle
        window (default 15s, ``TICKTICK_MCP_SYNC_TTL_SECONDS``); an edit made
        elsewhere within that window may not be visible yet. Call
        ``ticktick_sync`` to force an immediate refresh.

    Example:
        ticktick_get_by_id(obj_id="60ca9dbc8f08516d9dd56324")
    """
    try:
        client = TickTickClientSingleton.get_client()
        ensure_fresh(client)
        obj = client.get_by_id(obj_id)
        return format_response(obj)
    except Exception as exc:
        logger.error("ticktick_get_by_id failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- get_all ---


@mcp.tool()
@require_ticktick_client
async def ticktick_get_all(search: str, detail: str = DETAIL_COMPACT) -> str:
    """Dump everything of a single kind from the local sync state.

    Args:
        search (str): Either ``"tasks"``, ``"projects"`` or ``"tags"``
            (case-insensitive).
        detail (str, optional): ``"compact"`` (default) or ``"full"``.
            Accepted for parity with the other list tools and validated
            here. It has no effect on the ``"projects"``/``"tags"``
            searches (those return non-task records in full) nor on the
            currently inert ``"tasks"`` search -- for a compact task list
            use ``ticktick_filter_tasks`` or
            ``ticktick_get_tasks_from_project``.

    Returns:
        For ``"projects"``: JSON list, inbox prepended as
        ``{"id": <inbox>, "name": "Inbox"}``.
        For ``"tags"``: JSON list of tag objects.
        For ``"tasks"``: see Limitations.
        Unknown search type: ``{"error": "Invalid search type...",
        "status": "error"}``.

    Limitations:
        - ``"tasks"`` triggers a fetch of every open task across all
          projects, but the current implementation returns ``None``
          rather than a JSON string. Use
          ``ticktick_filter_tasks({"status": "uncompleted"})`` for a
          proper (compact) JSON response.

    Example:
        ticktick_get_all(search="projects")
    """
    try:
        normalise_detail(detail)
    except ValueError as exc:
        return format_response({"error": str(exc), "status": "error"})

    try:
        client = TickTickClientSingleton.get_client()
        client.sync()

        key = (search or "").strip().lower()
        if key == "tasks":
            # Documented quirk pinned by the test suite: result is
            # computed but not returned, so the tool yields Python None.
            _ = _get_all_tasks_from_ticktick()
            return None  # type: ignore[return-value]
        if key == "projects":
            projects = list(client.state.get("projects", []) or [])
            inbox_id = getattr(client, "inbox_id", None)
            result_list = []
            if inbox_id:
                result_list.append({"id": inbox_id, "name": "Inbox"})
            result_list.extend(projects)
            return format_response(result_list)
        if key == "tags":
            return format_response(list(client.state.get("tags", []) or []))

        return format_response(
            {
                "error": (f"Invalid search type {search!r}. Use 'tasks', 'projects', or 'tags'."),
                "status": "error",
            }
        )
    except Exception as exc:
        logger.error("ticktick_get_all failed: %s", exc, exc_info=True)
        return format_response({"error": str(exc), "status": "error"})


# --- sync ---


@mcp.tool()
@require_ticktick_client
async def ticktick_sync() -> str:
    """Force an immediate refresh of TickTick state from the server.

    The active-read tools (``ticktick_get_by_id``,
    ``ticktick_get_tasks_from_project``, ``ticktick_filter_tasks``) already
    auto-refresh at most once per throttle window (default 15s, overridable
    via ``TICKTICK_MCP_SYNC_TTL_SECONDS``). Call this when you need an
    immediate refresh -- e.g. you just changed something in the TickTick app
    on another device, or a read looks stale and you want to be certain
    before acting.

    Returns:
        ``{"status": "synced", "task_count": N, "project_count": M}`` on
        success. ``{"status": "error", "detail": "..."}`` if the refresh
        failed -- the previous (stale) state is still served by the read
        tools, so callers can continue with reduced confidence.

    Example:
        ticktick_sync()
    """
    try:
        client = TickTickClientSingleton.get_client()
        if not ensure_fresh(client, force=True):
            return format_response(
                {
                    "status": "error",
                    "detail": "sync failed; previous state is still being served",
                }
            )
        tasks = client.state.get("tasks", []) or []
        projects = client.state.get("projects", []) or []
        return format_response(
            {
                "status": "synced",
                "task_count": len(tasks),
                "project_count": len(projects),
            }
        )
    except Exception as exc:
        logger.error("ticktick_sync failed: %s", exc, exc_info=True)
        return format_response({"status": "error", "detail": str(exc)})
