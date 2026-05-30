"""Compact rendering for the list-returning task tools.

The list tools (``ticktick_get_tasks_from_project``, ``ticktick_filter_tasks``)
can return large task sets whose ``content`` blobs dominate the payload -- a
65-task project serialises to ~76k chars, enough to blow the MCP result cap
and force a dump-to-disk fallback. Compact mode keeps the light,
browsing-relevant fields plus a short ``contentPreview`` and drops the heavy
blobs (``content``, ``desc``, checklist ``items``) and bulky sync metadata
(``etag``, ``modifiedTime``, ``creator`` ...), cutting the same project to
~32k chars.

Full content for any single task stays reachable via ``ticktick_get_by_id``.

Compact mode is for browsing/listing ONLY. The TickTick API wipes any field
omitted from an update call, so the update round-trip must still fetch the
full object (``ticktick_get_by_id``) and send every field back via
``ticktick_update_task``. Compact output must never feed an update.
"""

import logging
from datetime import date
from typing import Any

from .helpers import _parse_due_date, format_response

logger = logging.getLogger(__name__)

DETAIL_COMPACT = "compact"
DETAIL_FULL = "full"
VALID_DETAILS = (DETAIL_COMPACT, DETAIL_FULL)

# Light fields kept verbatim in compact mode. Everything else -- notably the
# heavy content/desc/items blobs and bulky metadata (etag, modifiedTime,
# createdTime, creator, sortOrder, kind, focusSummaries ...) -- is dropped;
# the full object stays reachable via ticktick_get_by_id.
COMPACT_FIELDS = (
    "id",
    "projectId",
    "title",
    "dueDate",
    "startDate",
    "priority",
    "status",
    "isAllDay",
    "timeZone",
    "tags",
)

CONTENT_PREVIEW_CHARS = 200

# Stay comfortably below the MCP result cap (a ~76k-char full payload
# overflowed it). ~32k for 65 compact tasks leaves headroom to ~80 tasks
# before the in-band truncation note kicks in.
DEFAULT_CHAR_BUDGET = 40000

# Room reserved for the appended truncation note when trimming to budget.
_NOTE_RESERVE = 1000


def normalise_detail(detail: Any) -> str:
    """Return a validated detail level.

    Accepts case-insensitive ``"compact"`` / ``"full"`` (and ``None`` ->
    ``"compact"``). Raises ``ValueError`` on anything else so callers never
    silently fall back to the wrong level.
    """
    norm = detail or DETAIL_COMPACT
    if not isinstance(norm, str):
        raise ValueError(f"Invalid detail {detail!r}. Use 'compact' or 'full'.")
    norm = norm.strip().lower()
    if norm not in VALID_DETAILS:
        raise ValueError(f"Invalid detail {detail!r}. Use 'compact' or 'full'.")
    return norm


def _content_preview(task: dict) -> str:
    """First ``CONTENT_PREVIEW_CHARS`` of ``content`` (``""`` if none)."""
    content = task.get("content")
    if not content:
        return ""
    text = str(content)
    if len(text) <= CONTENT_PREVIEW_CHARS:
        return text
    return text[:CONTENT_PREVIEW_CHARS] + "..."


def compact_task(task: dict) -> dict:
    """Reduce a full task dict to its compact, browsing-friendly form."""
    if not isinstance(task, dict):
        return task
    out = {k: task[k] for k in COMPACT_FIELDS if k in task}
    out["contentPreview"] = _content_preview(task)
    # Defensive: never drop verification warnings if a caller attached them.
    if "_verification_warnings" in task:
        out["_verification_warnings"] = task["_verification_warnings"]
    return out


def _due_sort_key(task: Any):
    """Sort key ordering tasks soonest-due first, undated tasks last."""
    parsed = _parse_due_date(task.get("dueDate")) if isinstance(task, dict) else None
    return (parsed is None, parsed or date.min)


def render_task_list(
    tasks: Any,
    detail: str = DETAIL_COMPACT,
    char_budget: int = DEFAULT_CHAR_BUDGET,
) -> str:
    """Serialise a task list for an MCP list tool.

    ``detail="full"`` returns the raw objects unchanged (byte-for-byte what
    the tools returned before compact mode existed). ``detail="compact"``
    (the default) drops heavy fields and, if the compact payload would still
    exceed ``char_budget``, returns the soonest-due tasks up to the budget and
    appends an explicit truncation note. Nothing is ever dropped silently.
    """
    # Defensive: callers pass lists, but never crash on a stray shape.
    if not isinstance(tasks, list):
        return format_response(tasks)

    if detail == DETAIL_FULL:
        return format_response(tasks)

    compacted = [compact_task(t) for t in tasks]
    rendered = format_response(compacted)
    if len(rendered) <= char_budget:
        return rendered

    # Over budget: keep soonest-due tasks, append an explicit omission note.
    ordered = sorted(compacted, key=_due_sort_key)
    kept: list = []
    for item in ordered:
        if len(format_response(kept + [item])) > char_budget - _NOTE_RESERVE:
            break
        kept.append(item)

    # Safety: never return zero tasks just because one was unusually large.
    if not kept and ordered:
        kept.append(ordered[0])

    omitted = len(compacted) - len(kept)
    note = {
        "_truncation_note": (
            f"Returned {len(kept)} of {len(compacted)} active tasks (soonest-due "
            f"first); {omitted} omitted to stay under the {char_budget}-char "
            "compact budget. Reach the rest with a narrower ticktick_filter_tasks "
            'query, detail="full", or ticktick_get_by_id for a specific task.'
        ),
        "_returned_count": len(kept),
        "_omitted_count": omitted,
        "_total_count": len(compacted),
    }
    return format_response(kept + [note])
