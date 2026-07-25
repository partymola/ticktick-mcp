# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Tasks can be marked off-limits to modification via `TICKTICK_MCP_PROTECTED_TASK_IDS`, a space- or comma-separated list of task IDs. `ticktick_update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask` refuse any call naming one, returning `outcome: "protected_task"` without sending a request that reads or writes the task; reads are unaffected and an unset variable leaves behaviour unchanged. A batch delete containing a protected ID is refused in full rather than partially applied, because a partial delete cannot be undone. Since TickTick propagates delete and move through subtasks, the three structural tools also refuse when a protected task is the parent or subtask of a task the caller named, resolved from already-synced local state at no extra request. IDs are compared ignoring surrounding whitespace, quotes and case, so a padded or recased ID cannot slip past a check the API itself would resolve.

## [0.1.3] - 2026-07-22

### Added
- The v2 session token from a successful login is now cached to disk (`config/.token-v2`, `0600`) and reused on the next start, so the server no longer re-runs the username/password login (`user/signon`) on every construction. That endpoint is the one TickTick rate-limits with HTTP 429, so reusing the token -- as a logged-in browser does -- avoids the throttle entirely. A stale cached token is detected (the startup sync rejects it), cleared, and replaced by a single fresh login that repopulates the cache.

## [0.1.2] - 2026-07-22

### Fixed
- A TickTick login rate-limit is now surfaced explicitly instead of as a generic "Could Not Complete Request". ticktick-py re-authenticates with username/password on every client construction, and TickTick throttles that `user/signon` endpoint with HTTP 429; the failure now carries the HTTP status code, tool calls return `status: "rate_limited"` with a clear "stop retrying, wait ~15-30 minutes" message, and the server backs its own initialisation retry off to 5 minutes on a 429 (`TICKTICK_MCP_RATELIMIT_RETRY_SECONDS`, default 300s) instead of the ordinary 60s, so it stops re-hitting the throttled login and prolonging the block.

## [0.1.1] - 2026-07-12

### Fixed
- Client initialisation failures are no longer cached for the lifetime of the process. A transient login failure (rate limit, network blip, TickTick outage) used to leave the server permanently returning "TickTick client not initialized" until restarted; initialisation is now retried after a cooldown (default 60s, `TICKTICK_MCP_INIT_RETRY_SECONDS`). The error response now includes the underlying failure message and states that a retry will happen automatically.

### Packaging
- Listed in the official MCP registry (`io.github.partymola/ticktick-mcp`) via a GHCR container image; a release workflow builds/pushes the image and publishes to the registry.
- The container image now builds with `uv` from `uv.lock`, so it installs the patched `ticktick-py` fork (a plain `pip install` pulled the broken upstream, whose login no longer works).

## [0.1.0] - 2026-06-09

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

[Unreleased]: https://github.com/partymola/ticktick-mcp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/partymola/ticktick-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/partymola/ticktick-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/partymola/ticktick-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/partymola/ticktick-mcp/releases/tag/v0.1.0
