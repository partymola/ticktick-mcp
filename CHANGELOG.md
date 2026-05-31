# Changelog

## [Unreleased]

### Added
- Initial release: MCP server for TickTick task management.
- Tools: create, update, complete, move, delete, filter, get-by-id, get-all, get-from-project, make-subtask, datetime conversion.
- Completion tracking: idempotent processing of completed tasks via local SQLite (`get_unprocessed_completions`, `mark_completion_processed`).
- Field-preserving partial updates (omitted fields are not cleared on the TickTick side).
- Day-of-week validation on `dueDate` to catch date/day-name mismatches.
- Read-after-write verification (`_verification_warnings` on mutated tasks).
- Dateless-task warning (tasks created without a `dueDate` get a warning that they will not trigger reminders).
- On-demand freshness: active-read tools and the read step of mutations re-sync server state, throttled (default 15s, `TICKTICK_MCP_SYNC_TTL_SECONDS`), so edits made in the TickTick app on other devices are visible without restarting the server. Sync failures are fail-soft (last-known state is served).
- `ticktick_sync` tool: force an immediate refresh and report task/project counts.
- Recurring-aware completion: `ticktick_complete_task` returns `outcome: "completed_recurring"` and `next_occurrence_id` when a recurring task rolls forward, instead of a misleading "status still indicates open" warning.
- Silent no-op detection: `ticktick_update_task` returns `outcome: "no_op"` with re-read guidance when the API echoes an empty response and a re-read confirms the change did not apply.
- Routable-reopen guidance: `ticktick_update_task` returns `outcome: "needs_project_id"` when the target id is not in local sync state (`get_by_id` returns `{}`, typical for a completed recurring-history occurrence) and no `projectId` was supplied — the projectId-less open-API update would silently no-op, so the tool skips the futile POST and asks for a `projectId` (which lets the reopen succeed) instead of dead-end retry advice.
- Recurring reopen guard: `ticktick_update_task` returns `outcome: "reopen_no_effect"` (an error) when the only substantive change is `status:0` on a recurring task that has already rolled forward — such a "reopen" of the series id changes nothing and does not undo the completion, so it is refused with an explanation instead of reading as success. Updates that also change another field proceed unchanged.
