# ticktick-mcp - agent guide

`CLAUDE.md` symlinks to this file. It orients AI agents and contributors working *in* the code, and deliberately does not repeat the user-facing docs:

- **What it is, install, auth, tools, config, CLI, usage** -> [README.md](README.md)
- **Dev environment, running tests, pre-commit hook, PR & security process** -> [CONTRIBUTING.md](CONTRIBUTING.md)

**This is a public open-source repository.** Read the Data Safety Rules before committing.

## Data Safety Rules

Before committing ANY change, verify:

- **No real task content** in code, tests, commits, or docs - no real task titles, project IDs, account IDs, or task IDs from a live TickTick account
- **No credentials** - no `CLIENT_ID`, `CLIENT_SECRET`, OAuth tokens, refresh tokens, account email/password
- **Test fixtures must use synthetic data** - mocked clients only, no fixtures captured from a real account
- **`config/` and `*.db` are gitignored for a reason** - never override this

The pre-commit hook (`scripts/check-no-data.sh`) automatically rejects database files, config secrets, and large files. Install after cloning:

```bash
ln -sf ../../scripts/check-no-data.sh .git/hooks/pre-commit
```

## Architecture

- **MCP layer** (`src/ticktick_mcp/`) wraps `ticktick-py` (the TickTick v2 API client) and exposes MCP tools.
- **Client lifecycle** (`src/ticktick_mcp/client.py`) - lazy singleton constructed on the first tool call (`ticktick-py` logs in and syncs during `__init__`). A failed construction is not permanent: it is retried after a cooldown (default 60s, env `TICKTICK_MCP_INIT_RETRY_SECONDS`), and the auth-gate error in `helpers.py` reports the underlying failure so callers know to retry rather than restart.
- **Tools** (`src/ticktick_mcp/tools/`) - one module per tool group (task tools, filter tool, conversion tool, completion-tracking tools).
- **Completion DB** (`src/ticktick_mcp/completion_db.py`) - local SQLite that tracks which completed tasks have been processed by an agent, so the same completion isn't acted on twice.
- **Verification** (`src/ticktick_mcp/verification.py`) - read-after-write check that compares what was sent to the API against what came back, attaching `_verification_warnings` to mutated tasks.
- **Freshness** (`src/ticktick_mcp/freshness.py`) - on-demand, throttled `client.sync()` so long-lived reads do not go stale (see below).

## Session token cache and failure classification

`ticktick-py` re-runs a full username/password login on every client construction, and TickTick throttles `user/signon` with HTTP 429. The v2 session token is therefore cached at `.token-v2` and injected on the next construction, skipping signon - the same thing a browser does by keeping its cookie.

That makes deleting the cache a destructive act, so it is gated on classification, not on "something went wrong":

- **Only a rejection clears the cache** - a status in `_REJECTION_CODES` (401, 403), meaning TickTick judged the credentials unusable. **Never widen it to a condition that clears on its own.** A 429 in that set is a feedback loop: it discards a valid session and immediately POSTs the endpoint that is throttling us. Everything else - a 5xx, a read timeout, a reset connection, an exception nobody anticipated - propagates with the cache intact, and `get_client`'s cooldown handles the retry. Pinned by `TestOnlyARejectionCostsTheCachedToken` in `tests/test_client_retry.py`.
- **Status comes from the exception, never from its message.** `_augmented_check_status_code` raises `TickTickHTTPError` carrying `status`; `is_rate_limited` reads that. Matching `"429"` against the text fires on any error whose message happens to contain those digits - a task id, a URL - putting the server into a five-minute cooldown and telling the agent to stop. Pinned by `test_a_message_that_merely_contains_429_is_not_a_rate_limit`.
- **An unreadable cache is deleted, not merely skipped.** `read_text` raises `UnicodeDecodeError`, a `ValueError` and not an `OSError`, so a non-UTF-8 file escaped before anything could clear it and every retry failed identically. Pinned by `test_an_undecodable_cache_is_deleted_rather_than_bricking_the_server`.
- **Both credential files are owner-only.** `.token-v2` is created 0600 rather than written and chmodded after, and the tightening is best-effort so it cannot cost the token. `.token-oauth` is written by `ticktick-py` with no mode of its own, so it is narrowed after construction. The config directory is created 0700. **All three are POSIX mode bits and mean nothing on Windows**, which governs access by inherited ACLs: `chmod` there sets only the read-only flag, so a file written 0600 reads back 0666 and no mode narrows who may read it. The tests asserting them skip on Windows rather than being made to pass. What still holds everywhere is pinned separately: the mode handed to `os.open`, that a failed tightening does not cost the token, and that the config directory is created at the configured path.

  The consequence for anyone editing this code: **the `fchmod` tightening, the OAuth-cache narrowing and the directory mode are pinned on the POSIX legs alone.** Delete the `fchmod` block outright and the Windows leg still comes back green, so a green Windows run is not evidence about the credential files' posture - only a POSIX run is.

### Seams the test suite does not cross

Three findings in this area came from the same place: a claim that is true of the code but lives where no test can reach it. Check these by execution, not by reading, whenever they change.

- **The `ticktick-py` monkeypatch layer.** `tests/conftest.py` replaces `ticktick.api.TickTickClient` with a `MagicMock` before any import, so `_augment_login` and `_augment_check_status_code` install onto a mock and every attribute of a mock answers yes. Drive them against a stand-in class, and pin the module-level wiring by reading the source.
- **The CLI process boundary.** A command in the README is not exercised by anything that imports the package. `test_the_readme_command_is_the_one_the_cli_implements` pins the one that drifted; run any new one before documenting it.
- **Exception-classification scope.** A `try` whose `except` decides *what failed* is an interface, and everything inside it is claiming to be that thing. `_is_rejection` answers "is the cached v2 token stale?", so only the expression that exercises that token belongs in the try - which is why `_new_oauth()` is built above it. Putting a third call back inside would make an OAuth failure read as a stale session token and cost it.

**When a change adds an entry point** - a subcommand, a flag, a tool, or a documented user action - the unit to review is not the diff. It is the set of existing paths whose reachability or frequency the new entry point changes.

## Freshness model

`ticktick-py` syncs its local `state` only once, at client construction, and the server is a long-lived process that is not the only writer (the same account is edited in the app on other devices). `freshness.ensure_fresh(client)` re-syncs on demand, throttled to at most one sync per window (default 15s, env `TICKTICK_MCP_SYNC_TTL_SECONDS`):

- Active-read tools (`ticktick_get_by_id`, `ticktick_get_tasks_from_project`, the uncompleted branch of `ticktick_filter_tasks`) sync before reading. The completed branch of the filter fetches live, so it is excluded from that sync. It is not sync-free in every case: see "Filter criteria" for the one path that forces a refresh there.
- `ticktick_update_task` / `ticktick_complete_task` force a sync before their pre-read, so the body they POST is not built on a stale snapshot.
- `ticktick_sync` forces an immediate refresh and reports task/project counts, for when an agent needs certainty now.
- `ticktick_get_all` calls `client.sync()` directly rather than `ensure_fresh`, so it can report the underlying failure instead of serving stale state silently. It therefore syncs unconditionally and does not update the throttle clock - a read straight after it syncs again.
- Sync failures are fail-soft: the last-known state is served and the tool still returns, with a short backoff so a failing API does not turn every read into a tight resync loop.

## Completion & update outcomes

`ticktick_complete_task` tags its result with an additive `outcome`:

- `completed_recurring` (with `next_occurrence_id`) when a recurring task rolls forward on completion - the same id reappears as the next occurrence (status 0, due date advanced). This replaces a misleading "status still indicates open" warning.
- `completed` on every other success: a task refetched at status 2, and a task that left the active list and cannot be refetched (which keeps its existing note).
- `uncertain` when a non-recurring task is still open after completing. Something did not take, and labelling it `completed` would assert a success the code cannot back.

Every success path is tagged, so a caller branches on `outcome` alone and never has to read warning text. That matters most for `completed_recurring`, which comes back at status 0 and reads as a failure to anything checking status.

`ticktick_update_task` tags its result with an additive `outcome`:

- `needs_project_id` when the target id is not in local sync state (`get_by_id` returns `{}`) - typically a completed recurring-history occurrence (its status-2 record is never synced locally) or an unknown id - **and** no `projectId` was supplied. Without a routable `projectId` the open-API update silently no-ops (returns `""`), so the tool skips the futile POST and asks for the one thing that makes it work: re-call with `projectId` set on the task object. A completed recurring occurrence reopens cleanly once `projectId` is supplied.
- `updated` when the API echoed an empty response but a re-read confirms the change did land - a delayed confirmation, not a failure.
- `no_op` when the API echoes an empty response and a re-read confirms the change did not apply. Re-read with `ticktick_get_by_id` to confirm the current state before retrying.
- `reopen_no_effect` (an error) when the caller's only substantive change is `status:0` on a recurring task that has already rolled forward (it is back at status 0). Completing a recurring task advances the same id and files the completed instance as a separate history record, so a `status:0` "reopen" of the series id changes nothing and does **not** undo the completion - rather than let that read as success, the tool refuses and explains. Any update that also changes another field proceeds normally, so reschedules are unaffected.

## Project resolution

`projects.resolve_project_id(client, value)` accepts a project name where an id is expected. It sits outside `tools/` because every tool group needs it. Homing it in one tool module would deepen the one cross-import there already is (`completion_tools` reaches into `filter_tools` for `TaskFilterer`) - that import is the one to stop repeating, not to copy.

Contract:

- **Ids win.** A value matching a known project id (or the inbox id) short-circuits before name matching. The only case where this is load-bearing is a project *named* with another project's id - without it, that resolves to the wrong project.
- Names match case-insensitively after trimming, and after NFC normalisation on both sides (`_fold`). Normalisation is not cosmetic: an accented name stored composed and typed decomposed differs by code point, and casefold does not reconcile the two. It matters more since `filter_tasks` began confirming the result, because an unresolved name there is reported as a project the account does not have. **It widens the match set, and the reason is that both sides go through `_fold` before comparison, so any pair equal before is still equal.** Do not justify it as "NFC of a composed string is itself", which only covers names already composed. **The widening reaches the ambiguity raise too:** two projects whose names differ only by normal form used to resolve to whichever was byte-equal and now raise. That is the honest answer rather than a regression, but it is a refusal a caller could not previously hit. `state["projects"]` is `projectProfiles` and excludes the inbox, so the `"Inbox"` special-case cannot double-match a user project of the same name.
- **`_fold` governs NAME matching only.** `is_known_project_id` still compares ids with `value.strip()`, so no id is ever case-folded or re-normalised and the completion database cannot be keyed on a rewritten one.
- **Which normal form is not observable.** `_fold`'s output is only ever compared, never returned, so NFD would match identically and a test asserting NFC specifically would pin nothing.
- **Ambiguity raises**, naming every candidate id. Sync order is not a tie-break anyone chose, and a wrong answer files the task where the caller will not look.
- **Everything else passes through untouched.** Local state lags and id formats are the server's business; rejecting an unrecognised value would break callers that work today. That makes the change purely additive apart from the ambiguity raise.

The completion tools are stricter than the rest, because their resolved value is the completion database's key: they refuse a project they cannot confirm, and distinguish the two reasons - `outcome: "project_list_unverifiable"` when the project list could not be refreshed (retry), versus a plain error naming the value when the list is current and holds no such project. `filter_tasks` refuses on the same two-outcome shape, for a different reason given below. In the remaining tools an unresolvable value passes through unchanged: `get_tasks_from_project` and `move_task` then fail in `ticktick-py`'s own local lookup before any request is sent (`managers/tasks.py`, `get_by_fields` and `get_by_id`), while `create_task`, `update_task` and `delete_tasks` send it to the API. Nothing here tests what the API answers, so do not write that the API rejects it.

Called by every surface that takes a project id: `create_task`, `get_tasks_from_project`, `delete_tasks`, `move_task`, `update_task` (its `projectId` field), `filter_tasks`, and both completion tools - where the value is also the completion-DB key, so resolving keeps name-callers and id-callers on one key.

**Freshness belongs to the reader, not the caller.** The resolver reads `client.state["projects"]`, which only `sync()` repopulates, so a stale snapshot makes a name fall through untouched and then match nothing - `filter_tasks` returned an empty list with no error, which reads as a real answer. `resolve_project_id` therefore calls `ensure_fresh` itself, and only when it has a name to match: an id short-circuits before any sync, so an id caller pays nothing on the completed branch. A name still costs the resolver's throttled refresh, and `confirm_project_id` can force a second one, but only on its way to a refusal. `_protected_relation_refusal` does the same with `force=True`, after its own no-protection short-circuit, unless the caller has already forced one and says so via `already_fresh`.

**Do not add an `ensure_fresh` purely to serve resolution or the guard.** That requirement was enforced by convention across eight sites and five got it wrong - placed after the guard it was meant to protect, or omitted entirely. It now lives inside the three functions that need it - `resolve_project_id`, `confirm_project_id` and `_protected_relation_refusal` - so a further surface inherits it by calling one of them.

The `ensure_fresh` calls that remain in the tools serve the tool's **own** reads, and must not be removed: the task fetch in `get_tasks_from_project`; the lookup in `get_by_id`; the uncompleted branch of `filter_tasks`; and the forced pre-read in `update_task`, `complete_task`, `delete_tasks`, `move_task` and `make_subtask`. The last three were each deleted once: `delete_tasks` falls back to the caller's `projectId` when a task is missing from the snapshot, so it deletes from the wrong project and reports success; `move_task` takes `fromProjectId` out of the fetched body; `make_subtask` reads both ends. All five force rather than throttle, because a mutation posts back what it read and a 15-second-old snapshot is enough to get it wrong.

In the three structural tools that forced sync comes **first**, and the guard is then handed *its result* as `already_fresh`, so protected mode costs one full-account sync per call rather than two. Pass the result, never a literal `True`: a forced sync that failed returns `False`, and the guard has to know that so it can retry and - if that also fails - refuse rather than decide on a snapshot it could not update. If you add a tool that forces its own refresh before calling the guard, pass the flag; if you do not force one, do not pass it.

One consequence worth keeping in mind when editing the resolver: the id short-circuit runs on pre-sync state, so the sync can introduce the very project an id belongs to. There is a second id check after the sync for that reason - without it the id falls through to name matching and a project *named* with that id string wins it.

**The raise must be caught in each caller.** `ToolLogicError` is deliberately not a `ValueError` subclass, so a tool whose `except` only lists `ValueError` lets an ambiguous name escape as an unhandled exception instead of an error response. `tests/test_project_resolution.py` parametrises an ambiguity probe over every surface for exactly this reason - it is the cheapest check that the wiring exists *and* that its error path works.

## Filter criteria

**No value in `filter_criteria` reaches the query unchecked, and an unrecognised key is refused rather than dropped.** `ticktick_filter_tasks` filters locally, so nothing downstream ever rejects a bad value: it simply matches no task, and the caller is handed `[]`, which a model reads as "no tasks match" rather than "that value was invalid". An unknown `tz` was worse still, because it returned real rows under a zone that was never applied. Pinned by `tests/test_filter_criteria_validation.py`.

The checks that need no client live in `_build_property_filter`; the one that needs the account is `projects.confirm_project_id`, which both confirming tools call. What a maintainer could reasonably undo:

- **The date parse stays lenient in `PeriodFilter`; only the entry point insists.** `PeriodFilter`'s documented contract is that an unparseable bound becomes `None` and a no-bound window matches anything, pinned by `test_invalid_iso_string_returns_none` and `test_unparseable_task_date_with_no_filter_returns_true`. Moving the refusal into the validator changes a class `completion_tools` also constructs.
- **A project list that could not be REFRESHED is not grounds to refuse an id.** `confirm_project_id` forces a refresh before refusing, and reports `outcome: "project_list_unverifiable"` if that refresh fails, because a cache that could not be read is nothing to judge against. Refusing on it tells the caller their project does not exist, which is a claim about the account the code cannot back. A list that refreshed successfully and holds no such project is a different answer, and that one does refuse. Pinned by `test_a_project_list_that_could_not_be_refreshed_does_not_deny_the_project`.
- **That refresh must be FORCED, and its result re-resolved.** A throttled call reports fresh without syncing, so a project created since the last sync would be refused as missing. The failed-refresh test above cannot pin this, because a raising sync fails whether or not it was forced; `test_a_stale_snapshot_is_refreshed_before_the_project_is_refused` is the one that does, and it drives the throttle window directly.
- **The re-resolve's RETURN VALUE is the one that must be used, and only a NAME can catch it.** Given an id, the second `is_known_project_id` flips true off the refreshed snapshot whether or not the assignment was kept, so an id-based test passes over a dropped binding. `test_the_re_resolved_value_is_the_one_used` uses a name for that reason.
- **The re-resolve is gated on the refresh having SUCCEEDED, and this is where the two copies had silently drifted.** `ticktick-py` assigns `state["projects"]` partway through `sync`, before the keys it reads next, so a response missing a later key leaves the project list populated and then raises. `ensure_fresh` returns `False`. Re-checking outside the gate would then answer off a list that was never confirmed, which contradicts the bullet above; the gate is the documented side. Pinned by `test_a_refresh_that_populates_then_fails_is_not_treated_as_read`.
- **A verdict a call site does not recognise must refuse, at EVERY site.** `confirm_project_id` returns a token, so a third one is one edit away, and the two consequences differ: the completion tools would key a row on an unresolved value, and the filter would drop the project criterion and answer with tasks from every project, which is a confident wrong answer rather than an empty one. Both sites therefore end in an unconditional refusal, and `test_a_verdict_neither_site_recognises_refuses_rather_than_succeeding` is parametrised over all three tools for exactly that reason - pinning one site and documenting both is how this was first got wrong. `ProjectVerdict` is a `Literal`, but no type checker runs in CI or the dev extras, so read it as documentation rather than as enforcement; the test is the whole defence.
- **A refresh that SUCCEEDS but leaves nothing readable is also not evidence.** `confirm_project_id` additionally requires `isinstance(client.state, dict)` before refusing, the same predicate the protected-task relation guard uses, so a client carrying `state` as `None` or as a truthy non-dict answers `project_list_unverifiable` rather than naming a project as absent. Pinned by `test_a_state_that_cannot_be_read_is_not_a_missing_project` and, for the completion tools, `test_completion_tools_say_unverifiable_when_the_list_cannot_be_read`. **The residual is deliberate:** a dict state whose `projects` value is corrupt still reads as a genuine miss, because tightening the predicate to the list would turn a legitimately empty account into an `unverifiable` no caller could ever clear.
- **An inverted date window is refused, compared as DATES.** `contains()` is day-granular, so times running backwards inside one day still mean that day and must be accepted; only `start.date() > end.date()` is a window that can match nothing. The refusal quotes the caller's raw strings, because `PeriodFilter` converts an aware bound to server-local naive and a message built from the parsed value would name a date nobody typed, varying by host for a mixed naive and aware pair. An inverted `due` window is refused even under `status="completed"`, where that window is never read, which matches what the unparseable-date check already does.
- **`sort_by_priority` reads `None` as unset**, because every other criterion treats an explicit null as an absent key, and a model emitting JSON writes one. `tz` treats a blank string the same way, while `_checked_text` refuses one: there a blank is a filter value that is missing, here it is the absence of a filter.
- **`priority` rejects `bool` explicitly, and `status` is type-checked before the membership test.** `bool` is an `int` subclass and `True == 1`, so a bare membership test lets `True` through and silently filters for Low; `3.0 in (0, 1, 3, 5)` is `True` as well, so the `isinstance` guard is load-bearing rather than defensive. Set membership on an unhashable `status` raises `TypeError`, which is not a `ValueError` and escapes every handler the tool has, reaching the model as an error naming only the tool. Same shape guards `sort_by_priority`, where `bool("false")` is `True`.
- **A text criterion is trimmed, not merely checked.** `tag_label` is matched by exact membership against a task's tags, so returning a padded value would answer with the empty list this check exists to prevent.

The forced refresh sits on the refusal path only, so a correct id costs nothing extra and a correct name costs only the resolver's throttled refresh. An unrecognised project id on the otherwise sync-free `status="completed"` branch costs up to two full-account syncs before the refusal, one from the resolver and one forced, or one when the resolver's call falls inside the TTL.

**A closed project is still a known project.** A record in `state["projects"]` carries a `closed` field, so closing one does not remove it from the list and `is_known_project_id` still finds it. Verified against the record schema rather than against a closed project, so treat it as strong evidence rather than a measurement.

**`PeriodFilter._coerce_tz` still drops an unknown zone** rather than refusing it. Nothing inside the package reaches that branch any more, because `_build_property_filter` hands it a `ZoneInfo` or `None`, but constructing the model directly reinstates the behaviour this section exists to remove.

**A date bound is checked to the day, not to the character.** `PeriodFilter` falls back to the first ten characters, so `2026-09-01T25:99:99` is accepted as `2026-09-01` rather than refused. `contains()` compares at date granularity, so the window that runs is the one the day-part asked for.

## Protected tasks

`TICKTICK_MCP_PROTECTED_TASK_IDS` (space- or comma-separated) names tasks no mutating tool may change. The guard is two stages, both in `task_tools.py`. Both return `outcome: "protected_task"` on a hit; the second can also return `outcome: "protection_unverifiable"` when it cannot read local state and therefore cannot rule a protected relation in or out - either because the refresh failed or because `client.state` is not a dict (the predicate is `isinstance`, so a `Mapping` that is not a dict is refused too - no ticktick-py client carries one):

- **`_protected_refusal(ids)`** - a pure id comparison, the first statement of `update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask`.
- **`_protected_relation_refusal(client, ids)`** - catches a protected task reached through a task nobody named. TickTick propagates delete and move through subtasks, and a reparent restructures a task that was not an argument. Runs in `delete`, `move` and `make_subtask`. Forces a refresh itself before deciding, so it adds one request per call while protection is configured and none when it is not; if that refresh fails it refuses rather than deciding on a snapshot it could not update, because a wrong answer here permits a delete that cannot be undone.

Invariants, each pinned by a test in `tests/test_protected_tasks.py` or `tests/test_protected_tasks_gaps.py`, and each verified to fail against a deliberately broken build:

- **No request that reads or writes the task is ever sent.** Stage one runs before the tool touches the client at all. Note the narrower wording: `@require_ticktick_client` wraps every one of these tools and may establish a session first, so "before any network call" would be false on a cold server.
- A batch delete containing one protected ID is refused **whole**. Partial deletion cannot be undone.
- `make_subtask` guards **both** ends; so does the relation stage.
- Caller ids and configured ids go through the same `_norm_task_id` funnel (strip, unquote, casefold). Normalising only the configured side let a padded or recased id through to the API, which resolves it anyway.
- `update_task` accepts a raw dict as well as a `TaskObject`, and the guard runs before that normalisation, so it reads the ID from either shape.
- An unset variable means no protection and no behaviour change.
- Reads are never blocked.

- A failed refresh and a `client.state` that is not a dict are both refusals. Not "anything it cannot read" - a lookup that raises mid-walk still allows, which is the known limit below. The two that refuse are the ones where nothing could be read *at all*, and reading that as "no relations" is the fail-open answer: it looks identical to the fail-safe one in a test whose mock state iterates as empty.

**Known limit, deliberate:** the relation stage can only see relations present in local state. If a lookup raises, that id contributes nothing and the rest of the batch is still checked - it never abandons the batch, but it also cannot refuse on a relation it could not resolve. Do not describe this stage as a guarantee against every indirect mutation; stage one is the hard guarantee, stage two is defence in depth.

When adding a mutating tool, add both guards and a refusal test with it.

## Seams the suite does not cross

A claim sits at a seam when no input to the program can make its test fail. Behavioural tests answer "given this input, what happens"; these are about packaging and deployment. Check them by execution when they change.

- **The container's storage contract.** Four places must name the same directory and each is free to drift alone: the Dockerfile's `ENV`/`VOLUME`, the `-v` runtime argument in `server.json`, the README's `docker run` lines, and the Configuration table row that tells a reader what the image sets. `tests/test_packaging.py` pins all four by reading the sources. Two parser rules there are load-bearing, and `TestTheParserRulesThemselves` drives each against fixture text rather than only through the real Dockerfile: only instructions after the **final** `FROM` count, because this image is multi-stage and an `ENV` in the build stage never reaches the runtime image; and both `ENV k=v` and the legacy `ENV k v` forms must be read, or a later space-form line silently overrides the path. What it cannot see is whether the running process actually receives that environment - build the image and probe it.
- **Why the volume is not cosmetic here, and why an empty one is worse than none.** `/data` holds the `.env`, the cached OAuth token, the v2 session token and `completion_tracking.db`. Everything under it must derive from `config.dotenv_dir_path`: the database by `TestEveryPieceOfLocalStateSitsOnTheVolume`, the two token files incidentally by the permission tests in `tests/test_client_retry.py`. **Mount a directory that is already authorised, never a fresh volume:** the OAuth step opens a browser and reads a pasted URL from stdin, which for a stdio server is the JSON-RPC channel, so a container with no cached token silently consumes one client request per retry instead of failing. `ticktick-py` never prints the URL, so an in-container `auth` cannot be built - authorising on a host and mounting that directory is the only route.

  **The registry entry therefore ships no default mount, and cannot.** A bare name is a named volume, created empty; a relative path binds under whatever directory the client happens to be in; and `~` is rejected by docker outright, because a client exec'ing it directly has no shell to expand the tilde. All three were measured. It carries an absolute `placeholder` and `isRequired`, so the user supplies their own path - pinned by `test_the_mount_source_is_prompted_for_rather_than_defaulted`.
- **The registry's credential declaration.** `server.json` names the four `TICKTICK_*` variables and marks the secret and password `isSecret`. Nothing at runtime reads that file, so `test_the_credential_variables_are_declared` holds it to a list maintained alongside it in the test - not to `config.py`, which nothing connects it to. Add a variable in one place and the other two do not follow.

  **`TICKTICK_REDIRECT_URI` is deliberately excluded, and the exact-set assertion is why this is written down.** It is read only during host-side `auth`, never by the container, so declaring it would invite a container user to set something that changes nothing. Adding a `TICKTICK_*` variable to `config.py` therefore turns that test red with no explanation; decide whether the container needs it, and widen the set only if it does.

  **On an anonymous volume `/data` is created 0755 root-owned**, and `config.py`'s `mkdir(..., exist_ok=True, mode=0o700)` does not chmod a directory that already exists. Measured: a bind mount of a 0700 host directory keeps 0700 and the caller's ownership, so **the owner-only intent holds on the only documented route** and fails only on the anonymous volume every doc here tells users not to use. Do not read this as a general permissions hole and chase a chmod-at-startup fix for it; the files themselves are written owner-only either way.
- **The pre-commit guard.** `scripts/check-no-data.sh` is not exercised by pytest. Stage a probe file per class and check reject against expectation. **Never name a probe after a real file** - `config/` holds `.env`, `.token-oauth` and `.token-v2`, and a probe that truncates and deletes its path destroys them.

## Test conventions

- Async tools are tested via `asyncio.run()` wrapper.
- Mock `TickTickClientSingleton.get_client()` for all tests; never hit the live API.
- Group tests by behaviour class in `tests/test_*.py`.
- **Never `importlib.reload` a module other modules have already imported from.** Reload rebinds every name, so a class like `ToolLogicError` becomes a second object while everything that imported it earlier keeps the first. `except ToolLogicError` then stops matching a real raise, and the suite turns order-dependent - green alone, red in full, or the reverse. `tests/conftest.py` stashes `_original_require_ticktick_client` before patching so `test_helpers.py` can test the real decorator without reloading; do the same for anything else conftest clobbers.
