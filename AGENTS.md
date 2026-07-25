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
- `ticktick_get_all` calls `client.sync()` directly rather than `ensure_fresh`, so it can report the underlying failure instead of serving stale state silently. It therefore syncs unconditionally and does not update the throttle clock — a read straight after it syncs again.
- Sync failures are fail-soft: the last-known state is served and the tool still returns, with a short backoff so a failing API does not turn every read into a tight resync loop.

## Completion & update outcomes

`ticktick_complete_task` tags its result with an additive `outcome`:

- `completed_recurring` (with `next_occurrence_id`) when a recurring task rolls forward on completion — the same id reappears as the next occurrence (status 0, due date advanced). This replaces a misleading "status still indicates open" warning.
- `completed` on every other success: a task refetched at status 2, and a task that left the active list and cannot be refetched (which keeps its existing note).
- `uncertain` when a non-recurring task is still open after completing. Something did not take, and labelling it `completed` would assert a success the code cannot back.

Every success path is tagged, so a caller branches on `outcome` alone and never has to read warning text. That matters most for `completed_recurring`, which comes back at status 0 and reads as a failure to anything checking status.

`ticktick_update_task` tags its result with an additive `outcome`:

- `needs_project_id` when the target id is not in local sync state (`get_by_id` returns `{}`) — typically a completed recurring-history occurrence (its status-2 record is never synced locally) or an unknown id — **and** no `projectId` was supplied. Without a routable `projectId` the open-API update silently no-ops (returns `""`), so the tool skips the futile POST and asks for the one thing that makes it work: re-call with `projectId` set on the task object. A completed recurring occurrence reopens cleanly once `projectId` is supplied.
- `updated` when the API echoed an empty response but a re-read confirms the change did land — a delayed confirmation, not a failure.
- `no_op` when the API echoes an empty response and a re-read confirms the change did not apply. Re-read with `ticktick_get_by_id` to confirm the current state before retrying.
- `reopen_no_effect` (an error) when the caller's only substantive change is `status:0` on a recurring task that has already rolled forward (it is back at status 0). Completing a recurring task advances the same id and files the completed instance as a separate history record, so a `status:0` "reopen" of the series id changes nothing and does **not** undo the completion — rather than let that read as success, the tool refuses and explains. Any update that also changes another field proceeds normally, so reschedules are unaffected.

## Project resolution

`projects.resolve_project_id(client, value)` accepts a project name where an id is expected. It sits outside `tools/` because every tool group needs it. Homing it in one tool module would deepen the one cross-import there already is (`completion_tools` reaches into `filter_tools` for `TaskFilterer`) - that import is the one to stop repeating, not to copy.

Contract:

- **Ids win.** A value matching a known project id (or the inbox id) short-circuits before name matching. The only case where this is load-bearing is a project *named* with another project's id — without it, that resolves to the wrong project.
- Names match case-insensitively after trimming. `state["projects"]` is `projectProfiles` and excludes the inbox, so the `"Inbox"` special-case cannot double-match a user project of the same name.
- **Ambiguity raises**, naming every candidate id. Sync order is not a tie-break anyone chose, and a wrong answer files the task where the caller will not look.
- **Everything else passes through untouched.** Local state lags and id formats are the server's business; rejecting an unrecognised value would break callers that work today. That makes the change purely additive apart from the ambiguity raise.

The completion tools are stricter than the rest, because their resolved value is the completion database's key: they refuse a project they cannot confirm, and distinguish the two reasons — `outcome: "project_list_unverifiable"` when the project list could not be refreshed (retry), versus a plain error naming the value when the list is current and holds no such project. Everywhere else an unresolvable value simply passes through to the API.

Called by every surface that takes a project id: `create_task`, `get_tasks_from_project`, `delete_tasks`, `move_task`, `update_task` (its `projectId` field), `filter_tasks`, and both completion tools — where the value is also the completion-DB key, so resolving keeps name-callers and id-callers on one key.

**Freshness belongs to the reader, not the caller.** The resolver reads `client.state["projects"]`, which only `sync()` repopulates, so a stale snapshot makes a name fall through untouched and then match nothing — `filter_tasks` returned an empty list with no error, which reads as a real answer. `resolve_project_id` therefore calls `ensure_fresh` itself, and only when it has a name to match: an id short-circuits before any sync, so id callers pay nothing and the completed branch of `filter_tasks` stays sync-free. `_protected_relation_refusal` does the same with `force=True`, after its own no-protection short-circuit, unless the caller has already forced one and says so via `already_fresh`.

**Do not add an `ensure_fresh` purely to serve resolution or the guard.** That requirement was enforced by convention across eight sites and five got it wrong — placed after the guard it was meant to protect, or omitted entirely. It now lives inside the two functions that need it, so a ninth surface inherits it by calling them.

The `ensure_fresh` calls that remain in the tools serve the tool's **own** reads, and must not be removed: the task fetch in `get_tasks_from_project`; the lookup in `get_by_id`; the uncompleted branch of `filter_tasks`; and the forced pre-read in `update_task`, `complete_task`, `delete_tasks`, `move_task` and `make_subtask`. The last three were each deleted once: `delete_tasks` falls back to the caller's `projectId` when a task is missing from the snapshot, so it deletes from the wrong project and reports success; `move_task` takes `fromProjectId` out of the fetched body; `make_subtask` reads both ends. All five force rather than throttle, because a mutation posts back what it read and a 15-second-old snapshot is enough to get it wrong.

In the three structural tools that forced sync comes **first**, and the guard is then handed *its result* as `already_fresh`, so protected mode costs one full-account sync per call rather than two. Pass the result, never a literal `True`: a forced sync that failed returns `False`, and the guard has to know that so it can retry and — if that also fails — refuse rather than decide on a snapshot it could not update. If you add a tool that forces its own refresh before calling the guard, pass the flag; if you do not force one, do not pass it.

One consequence worth keeping in mind when editing the resolver: the id short-circuit runs on pre-sync state, so the sync can introduce the very project an id belongs to. There is a second id check after the sync for that reason — without it the id falls through to name matching and a project *named* with that id string wins it.

**The raise must be caught in each caller.** `ToolLogicError` is deliberately not a `ValueError` subclass, so a tool whose `except` only lists `ValueError` lets an ambiguous name escape as an unhandled exception instead of an error response. `tests/test_project_resolution.py` parametrises an ambiguity probe over every surface for exactly this reason — it is the cheapest check that the wiring exists *and* that its error path works.

## Protected tasks

`TICKTICK_MCP_PROTECTED_TASK_IDS` (space- or comma-separated) names tasks no mutating tool may change. The guard is two stages, both in `task_tools.py`. Both return `outcome: "protected_task"` on a hit; the second can also return `outcome: "protection_unverifiable"` when it cannot read local state and therefore cannot rule a protected relation in or out - either because the refresh failed or because `client.state` is not a dict (the predicate is `isinstance`, so a `Mapping` that is not a dict is refused too - no ticktick-py client carries one):

- **`_protected_refusal(ids)`** — a pure id comparison, the first statement of `update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask`.
- **`_protected_relation_refusal(client, ids)`** — catches a protected task reached through a task nobody named. TickTick propagates delete and move through subtasks, and a reparent restructures a task that was not an argument. Runs in `delete`, `move` and `make_subtask`. Forces a refresh itself before deciding, so it adds one request per call while protection is configured and none when it is not; if that refresh fails it refuses rather than deciding on a snapshot it could not update, because a wrong answer here permits a delete that cannot be undone.

Invariants, each pinned by a test in `tests/test_protected_tasks.py` or `tests/test_protected_tasks_gaps.py`, and each verified to fail against a deliberately broken build:

- **No request that reads or writes the task is ever sent.** Stage one runs before the tool touches the client at all. Note the narrower wording: `@require_ticktick_client` wraps every one of these tools and may establish a session first, so "before any network call" would be false on a cold server.
- A batch delete containing one protected ID is refused **whole**. Partial deletion cannot be undone.
- `make_subtask` guards **both** ends; so does the relation stage.
- Caller ids and configured ids go through the same `_norm_task_id` funnel (strip, unquote, casefold). Normalising only the configured side let a padded or recased id through to the API, which resolves it anyway.
- `update_task` accepts a raw dict as well as a `TaskObject`, and the guard runs before that normalisation, so it reads the ID from either shape.
- An unset variable means no protection and no behaviour change.
- Reads are never blocked.

- A failed refresh and a `client.state` that is not a dict are both refusals. Not "anything it cannot read" - a lookup that raises mid-walk still allows, which is the known limit below. The two that refuse are the ones where nothing could be read *at all*, and reading that as "no relations" is the fail-open answer: it looks identical to the fail-safe one in a test whose mock state iterates as empty.

**Known limit, deliberate:** the relation stage can only see relations present in local state. If a lookup raises, that id contributes nothing and the rest of the batch is still checked — it never abandons the batch, but it also cannot refuse on a relation it could not resolve. Do not describe this stage as a guarantee against every indirect mutation; stage one is the hard guarantee, stage two is defence in depth.

When adding a mutating tool, add both guards and a refusal test with it.

## Test conventions

- Async tools are tested via `asyncio.run()` wrapper.
- Mock `TickTickClientSingleton.get_client()` for all tests; never hit the live API.
- Group tests by behaviour class in `tests/test_*.py`.
- **Never `importlib.reload` a module other modules have already imported from.** Reload rebinds every name, so a class like `ToolLogicError` becomes a second object while everything that imported it earlier keeps the first. `except ToolLogicError` then stops matching a real raise, and the suite turns order-dependent — green alone, red in full, or the reverse. `tests/conftest.py` stashes `_original_require_ticktick_client` before patching so `test_helpers.py` can test the real decorator without reloading; do the same for anything else conftest clobbers.
