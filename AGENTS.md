# ticktick-mcp - agent guide

`CLAUDE.md` symlinks to this file. It orients AI agents and contributors working *in* the code, and deliberately does not repeat the user-facing docs:

- **What it is, install, auth, tools, config, CLI, usage** -> [README.md](README.md)
- **Dev environment, running tests, pre-commit hook, PR & security process** -> [CONTRIBUTING.md](CONTRIBUTING.md)

**This is a public open-source repository.** Read the Data Safety Rules before committing.

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

## Architecture

- **MCP layer** (`src/ticktick_mcp/`) wraps `ticktick-py` (the TickTick v2 API client) and exposes MCP tools.
- **Client lifecycle** (`src/ticktick_mcp/client.py`) — lazy singleton constructed on the first tool call (`ticktick-py` logs in and syncs during `__init__`). A failed construction is not permanent: it is retried after a cooldown (default 60s, env `TICKTICK_MCP_INIT_RETRY_SECONDS`), and the auth-gate error in `helpers.py` reports the underlying failure so callers know to retry rather than restart.
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
- `completed` on every other success: a task refetched at status 2, and a task that left the active list and cannot be refetched (which keeps its existing note).
- `uncertain` when a non-recurring task is still open after completing. Something did not take, and labelling it `completed` would assert a success the code cannot back.

Every success path is tagged, so a caller branches on `outcome` alone and never has to read warning text. That matters most for `completed_recurring`, which comes back at status 0 and reads as a failure to anything checking status.

`ticktick_update_task` tags its result with an additive `outcome` in two cases:

- `needs_project_id` when the target id is not in local sync state (`get_by_id` returns `{}`) — typically a completed recurring-history occurrence (its status-2 record is never synced locally) or an unknown id — **and** no `projectId` was supplied. Without a routable `projectId` the open-API update silently no-ops (returns `""`), so the tool skips the futile POST and asks for the one thing that makes it work: re-call with `projectId` set on the task object. A completed recurring occurrence reopens cleanly once `projectId` is supplied.
- `no_op` when the API echoes an empty response and a re-read confirms the change did not apply. Re-read with `ticktick_get_by_id` to confirm the current state before retrying.
- `reopen_no_effect` (an error) when the caller's only substantive change is `status:0` on a recurring task that has already rolled forward (it is back at status 0). Completing a recurring task advances the same id and files the completed instance as a separate history record, so a `status:0` "reopen" of the series id changes nothing and does **not** undo the completion — rather than let that read as success, the tool refuses and explains. Any update that also changes another field proceeds normally, so reschedules are unaffected.

## Protected tasks

`TICKTICK_MCP_PROTECTED_TASK_IDS` (space- or comma-separated) names tasks no mutating tool may change. The guard is two stages, both in `task_tools.py`, and both return `outcome: "protected_task"`:

- **`_protected_refusal(ids)`** — a pure id comparison, the first statement of `update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask`.
- **`_protected_relation_refusal(client, ids)`** — catches a protected task reached through a task nobody named. TickTick propagates delete and move through subtasks, and a reparent restructures a task that was not an argument. Runs in `delete`, `move` and `make_subtask` off already-synced local state, so it costs no extra request.

Invariants, each pinned by a test in `tests/test_protected_tasks.py` or `tests/test_protected_tasks_gaps.py`, and each verified to fail against a deliberately broken build:

- **No request that reads or writes the task is ever sent.** Stage one runs before the tool touches the client at all. Note the narrower wording: `@require_ticktick_client` wraps every one of these tools and may establish a session first, so "before any network call" would be false on a cold server.
- A batch delete containing one protected ID is refused **whole**. Partial deletion cannot be undone.
- `make_subtask` guards **both** ends; so does the relation stage.
- Caller ids and configured ids go through the same `_norm_task_id` funnel (strip, unquote, casefold). Normalising only the configured side let a padded or recased id through to the API, which resolves it anyway.
- `update_task` accepts a raw dict as well as a `TaskObject`, and the guard runs before that normalisation, so it reads the ID from either shape.
- An unset variable means no protection and no behaviour change.
- Reads are never blocked.

**Known limit, deliberate:** the relation stage can only see relations present in local state. If a lookup raises, that id contributes nothing and the rest of the batch is still checked — it never abandons the batch, but it also cannot refuse on a relation it could not resolve. Do not describe this stage as a guarantee against every indirect mutation; stage one is the hard guarantee, stage two is defence in depth.

When adding a mutating tool, add both guards and a refusal test with it.

## Test conventions

- Async tools are tested via `asyncio.run()` wrapper.
- Mock `TickTickClientSingleton.get_client()` for all tests; never hit the live API.
- Group tests by behaviour class in `tests/test_*.py`.
