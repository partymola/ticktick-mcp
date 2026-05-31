# ticktick-mcp

**This is a public open-source repository.** Every commit, PR, and file is visible to anyone.

## Data Safety Rules

Before committing ANY change, verify:

- **No real task content** in code, tests, commits, or docs — no real task titles, project IDs, account IDs, or task IDs from a live TickTick account
- **No credentials** — no `CLIENT_ID`, `CLIENT_SECRET`, OAuth tokens, refresh tokens, account email/password
- **Test fixtures must use synthetic data** — mocked clients only, no fixtures captured from a real account
- **`config/` and `*.db` are gitignored for a reason** — never override this

The pre-commit hook (`scripts/check-no-data.sh`) automatically rejects database files, config secrets, and large files. Install after cloning:

```bash
ln -sf ../../scripts/check-no-data.sh .git/hooks/pre-commit
```

## Quick Reference

```bash
ticktick-mcp           # Start MCP server (stdio transport, used by Claude Code)
```

## Architecture

- **MCP layer** (`src/ticktick_mcp/`) wraps `ticktick-py` (the TickTick v2 API client) and exposes MCP tools.
- **Tools** (`src/ticktick_mcp/tools/`) — one module per tool group (task tools, filter tool, conversion tool, completion-tracking tools).
- **Completion DB** (`src/ticktick_mcp/completion_db.py`) — local SQLite that tracks which completed tasks have been processed by an agent, so the same completion isn't acted on twice.
- **Verification** (`src/ticktick_mcp/verification.py`) — read-after-write check that compares what was sent to the API against what came back, attaching `_verification_warnings` to mutated tasks.
- **Freshness** (`src/ticktick_mcp/freshness.py`) — on-demand, throttled `client.sync()` so long-lived reads do not go stale (see below).

## Freshness model

`ticktick-py` syncs its local `state` only once, at client construction, and the server is a long-lived process that is not the only writer (the same account is edited in the app on other devices). `freshness.ensure_fresh(client)` re-syncs on demand, throttled to at most one sync per window (default 15s, env `TICKTICK_MCP_SYNC_TTL_SECONDS`):

- Active-read tools (`ticktick_get_by_id`, `ticktick_get_tasks_from_project`, the uncompleted branch of `ticktick_filter_tasks`) sync before reading. The completed branch of the filter fetches live, so it is excluded.
- `ticktick_update_task` / `ticktick_complete_task` force a sync before their pre-read, so the body they POST is not built on a stale snapshot.
- `ticktick_sync` forces an immediate refresh and reports task/project counts, for when an agent needs certainty now.
- Sync failures are fail-soft: the last-known state is served and the tool still returns, with a short backoff so a failing API does not turn every read into a tight resync loop.

## Completion & update outcomes

`ticktick_complete_task` tags its result with an additive `outcome`:

- `completed_recurring` (with `next_occurrence_id`) when a recurring task rolls forward on completion — the same id reappears as the next occurrence (status 0, due date advanced). This replaces a misleading "status still indicates open" warning.
- otherwise the existing behaviour stands; a non-recurring task that leaves the active list keeps the "task could not be re-fetched" success signal.

`ticktick_update_task` tags its result with an additive `outcome` in two cases:

- `needs_project_id` when the target id is not in local sync state (`get_by_id` returns `{}`) — typically a completed recurring-history occurrence (its status-2 record is never synced locally) or an unknown id — **and** no `projectId` was supplied. Without a routable `projectId` the open-API update silently no-ops (returns `""`), so the tool skips the futile POST and asks for the one thing that makes it work: re-call with `projectId` set on the task object. A completed recurring occurrence reopens cleanly once `projectId` is supplied.
- `no_op` when the API echoes an empty response and a re-read confirms the change did not apply. Re-read with `ticktick_get_by_id` to confirm the current state before retrying.
- `reopen_no_effect` (an error) when the caller's only substantive change is `status:0` on a recurring task that has already rolled forward (it is back at status 0). Completing a recurring task advances the same id and files the completed instance as a separate history record, so a `status:0` "reopen" of the series id changes nothing and does **not** undo the completion — rather than let that read as success, the tool refuses and explains. Any update that also changes another field proceeds normally, so reschedules are unaffected.

## Test conventions

- Async tools are tested via `asyncio.run()` wrapper.
- Mock `TickTickClientSingleton.get_client()` for all tests; never hit the live API.
- Group tests by behaviour class in `tests/test_*.py`.
