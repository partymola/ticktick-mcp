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
