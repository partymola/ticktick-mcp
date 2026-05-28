"""
MCP tools for tracking which TickTick task completions have been processed
by domain agents.

Tools:
    ticktick_get_unprocessed_completions -- returns completed tasks not yet seen
    ticktick_mark_completion_processed   -- marks one task as seen
"""

import datetime
import logging
from typing import Optional

from tzlocal import get_localzone

from ..mcp_instance import mcp
from ..helpers import format_response, require_ticktick_client
from ..completion_db import init_db, is_processed, mark_processed, get_processed_ids_for_project
from ..tools.filter_tools import TaskFilterer, PeriodFilter, PropertyFilter


@mcp.tool()
@require_ticktick_client
async def ticktick_get_unprocessed_completions(
    project_id: str,
    days: int = 30,
) -> str:
    """
    Returns completed tasks for a project that have NOT yet been marked as
    processed by a domain agent.

    Could be called at the beginning of each conversation to check for new
    completions. After reviewing each returned task, call
    ticktick_mark_completion_processed to record that it has been handled.

    Args:
        project_id (str): TickTick project ID to check. Required.
        days (int): How many days back to look for completions. Default 30.

    Returns:
        JSON list of unprocessed task objects (may be empty).
        Each object includes: id, title, projectId, completedTime, content.
        Error: {"error": "...", "status": "error"}

    Usage Guide:
        - Call once per project at the start of each conversation.
        - For each returned task: read the content field, log meaningful
          outcomes if appropriate, then call ticktick_mark_completion_processed.
        - Example:
            ticktick_get_unprocessed_completions(
                project_id="your_project_id_here",
                days=30
            )
    """
    init_db()

    tz = get_localzone()
    end_dt = datetime.datetime.now(tz)
    start_dt = end_dt - datetime.timedelta(days=days)

    completion_filter = PeriodFilter(
        start_date=start_dt.isoformat(),
        end_date=end_dt.isoformat(),
        tz=tz,
    )
    property_filter = PropertyFilter(
        status="completed",
        project_id=project_id,
        completion_date_filter=completion_filter,
    )

    try:
        filterer = TaskFilterer()
        all_completed = await filterer.filter(
            property_filter=property_filter,
            sort_by_priority=False,
            tz_info=tz,
        )
    except Exception as e:
        logging.error("Failed to fetch completions for project %s: %s", project_id, e, exc_info=True)
        return format_response({"error": str(e), "status": "error"})

    already_processed = get_processed_ids_for_project(project_id)
    unprocessed = [t for t in all_completed if t.get("id") not in already_processed]

    logging.info(
        "project %s: %d completed in last %d days, %d unprocessed",
        project_id, len(all_completed), days, len(unprocessed),
    )
    return format_response(unprocessed)


@mcp.tool()
async def ticktick_mark_completion_processed(
    task_id: str,
    project_id: str,
    title: Optional[str] = None,
    completed_time: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Records that a domain agent has processed a completed task.

    Call this after reviewing each task returned by
    ticktick_get_unprocessed_completions. The task will no longer appear
    in future calls to that tool.

    Args:
        task_id (str): Full TickTick task ID. Required.
        project_id (str): Project ID the task belongs to. Required.
        title (str, optional): Task title (stored for human-readable audit trail).
        completed_time (str, optional): ISO completion timestamp from the task object.
        notes (str, optional): Brief notes on how the completion was handled.

    Returns:
        {"status": "ok", "task_id": "..."} on success.
        {"status": "already_processed", "task_id": "..."} if already recorded.
        {"error": "...", "status": "error"} on failure.

    Usage Guide:
        - Call once per task after finishing your handling of it.
        - Example:
            ticktick_mark_completion_processed(
                task_id="abc123",
                project_id="your_project_id_here",
                title="Fix kitchen tap",
                completed_time="2025-06-01T19:00:00+01:00",
                notes="Replaced ceramic disc, resolved"
            )
    """
    init_db()

    if is_processed(task_id):
        return format_response({"status": "already_processed", "task_id": task_id})

    try:
        mark_processed(
            task_id=task_id,
            project_id=project_id,
            title=title,
            completed_time=completed_time,
            notes=notes,
        )
        return format_response({"status": "ok", "task_id": task_id})
    except Exception as e:
        logging.error("Failed to mark task %s as processed: %s", task_id, e, exc_info=True)
        return format_response({"error": str(e), "status": "error"})
