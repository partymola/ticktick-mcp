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
- Silent no-op detection: `ticktick_update_task` returns `outcome: "no_op"` with retry guidance when the API echoes an empty response and a re-read confirms the change did not apply (e.g. reopening a completed recurring occurrence).
