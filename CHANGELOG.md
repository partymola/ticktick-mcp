# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.4] - 2026-09-05

### Packaging

- The published image is rebuilt on the `python:3.14.7-slim` base, so it carries the upstream base updates issued since the pin was last moved. Those had stopped arriving: the pin named a digest of the floating `3.14-slim` tag, and automated updates read that tag as the version, so once a pull request existed for it a rebuild of the same tag was treated as already proposed. The pin now names the patch tag alongside the digest. The image is built with `uv` 0.12.7.
- `mcp` is pinned to 2.1.1, `pydantic` to 2.13.5 and `python-dotenv` to 1.2.3, each the version the suite runs against. The tool contract is unchanged: every tool keeps its name, description, and input and output schemas.

### Documentation

- The README states which Python versions and platforms are tested.

## [0.3.3] - 2026-08-21

### Packaging

- The package declares `Operating System :: OS Independent`, and CI runs the suite on Linux, macOS and Windows. It had only ever been tested on Linux while declaring nothing about platform support. The owner-only modes on the two credential files and the config directory are POSIX mode bits and do nothing on Windows, which governs access by inherited ACLs instead.

## [0.3.2] - 2026-08-09

### Fixed

- The container image keeps credentials and cached state on a mountable volume. The config directory defaulted to a path under the container's own home directory, which nothing told anyone to mount - so the `.env`, the cached OAuth token, the v2 session token and the completion-tracking database all went with the container. Every start then fell back to a password signon, and TickTick throttles those: a container restarted a few times locks itself out for 15-30 minutes. The image now sets `TICKTICK_MCP_DOTENV_DIR` to `/data` and declares it a volume. Installs from source are unaffected - the resolution order is unchanged, and only the image sets this variable.
- The registry entry declares what the image needs: the directory to mount, and the four `TICKTICK_*` credential variables for anyone supplying them directly instead of mounting a `.env`. The client secret and password are marked as secrets so a client handles them accordingly. Previously it declared nothing, so someone configuring this server from the MCP registry got a container with no credentials and no indication of what was missing.

  **Mount a directory that has already been authorised, not an empty volume.** The OAuth step opens a browser and waits for a URL pasted back on standard input - which for a stdio server is the JSON-RPC channel - so it cannot run inside the container, and a container without a cached token consumes client requests waiting for input rather than reporting anything. Run `ticktick-mcp auth` on a machine with a browser and mount the directory it wrote to. The registry entry asks for that path and will not run without it: no default can work, since a bare name is a named volume created empty, a relative path binds under the client's own working directory, and `~` is rejected by docker when it is exec'd without a shell.

  **If you were already working around this by mounting at the old in-container path** (`/root/.config/ticktick-mcp` or similar), move the mount to `/data` - the old path is no longer read, and a mount left there is silently unused.

### Documentation

- The README documents the container: where images are published, that tags carry a `v` prefix, that authorisation must happen on a machine with a browser and the resulting directory be mounted in, and how to pass credentials as environment variables while still mounting that directory for the token cache. The environment-variable form names each variable without a value, so nothing secret appears in the command or in shell history. It also notes that authorising needs a source install, since the PyPI name `ticktick-mcp` belongs to an unrelated project, and that `--user` is needed so the container does not leave root-owned files that stop a later host-side run caching its session token.

## [0.3.1] - 2026-08-09

### Added

- `ticktick-mcp auth` authorises at a terminal and exits. TickTick issues no refresh token, so the OAuth step opens a browser and asks for a URL to be pasted back - and there was no way to do that outside the server, where the prompt reads the JSON-RPC channel. An unauthorised first tool call therefore opened a browser on the host and blocked, and in a container could not be completed at all. Run this once before registering the server, and again when the token expires.

### Fixed

- The documented behaviour of the OAuth token was wrong: it does not refresh automatically, and there is no refresh token to do it with. The README now says what actually happens and when it recurs.
- A cached OAuth token that cannot be read is discarded and rebuilt rather than failing every start. Only unreadable-file errors were handled by the underlying library, so a file that was not valid JSON or not UTF-8 escaped construction entirely and every retry failed identically, reporting a JSON parse error with no hint at the cause.
- A cached session token is also discarded when the server's own startup calls come back in a shape it cannot read, not only when TickTick answers with an outright rejection. That case is limited to one attempt per run, so a change to TickTick's response format costs a single extra sign-in rather than one on every retry.

- A transient failure while resuming a cached session no longer discards the session token and re-runs the login. Any exception at all cleared the cache and immediately POSTed to `user/signon` - so a rate limit, a read timeout, a reset connection or a 5xx cost a working session and hit the one endpoint TickTick throttles, which is what the cache exists to avoid. Only an outright rejection, HTTP 401 or 403, clears it now; everything else propagates with the cache intact.
- A rate limit is recognised from the response status rather than by looking for "429" in the error text. An unrelated failure whose message happened to contain those digits - a task id, a URL - put the server into a five-minute cooldown and told the agent to stop retrying.
- A cached session token that cannot be decoded is discarded instead of bricking the server. Only unreadable-file errors were handled, so a file that was not valid UTF-8 failed before anything could clear it, made every retry fail identically, and reported as a credentials problem. It is now deleted and rebuilt on the next call.
- The cached session token is created with owner-only permissions rather than written first and narrowed afterwards, so it is never briefly world-readable, and a failure to narrow it can no longer leave it that way permanently. The OAuth token cache, written by the underlying library with no mode of its own, is narrowed too, and the config directory is created owner-only. On POSIX; Windows governs access by ACLs. Files created by an earlier version are narrowed the next time a token is saved - to check now, `ls -la` the config directory.

### Packaging

- The container image is built on Python 3.14 instead of 3.13, and 3.14 joins the supported-version classifiers. `requires-python` is unchanged at `>=3.13`: the package still supports both, and only the published image moves. The published container is the distribution channel here - the `ticktick-mcp` name on PyPI belongs to an unrelated project.
- Dependency updates are automated. Every dependency, the base image and the CI actions are pinned to exact versions, so nothing changes without a deliberate bump; Dependabot now proposes those bumps rather than leaving the pins to rot.

## [0.3.0] - 2026-08-03

### Changed

- Ported to the `mcp` 2.x server API. 2.0.0 renamed `mcp.server.fastmcp` to `mcp.server.mcpserver` and the `FastMCP` class to `MCPServer`, with no compatibility alias. The tool contract is unchanged: every tool keeps its name, description, and input and output schemas.
- Every dependency is pinned to an exact version instead of a lower bound: `mcp` 2.0.0, `anyio` 4.14.2, `python-dotenv` 1.2.2, `pydantic` 2.13.4 and `tzlocal` 5.4.4, and for development `pytest` 9.1.1, `pytest-asyncio` 1.4.0 and `ruff` 0.16.1.

### Fixed

- A fresh install no longer breaks on import. The `mcp` spec was `>=1.6.0` with no upper bound, so once 2.0.0 was published the resolver picked it and the server failed to start.
- Strict tool-argument validation is now pinned by a test that invokes a registered tool with an unknown kwarg, rather than only asserting the setting the patch writes. The strictness comes from patching a private `mcp` internal; if that internal stops being what argument models are built on, the patch still applies cleanly to an object nothing reads and typo'd kwargs silently fall through to their defaults again - which the previous checks could not distinguish from working.

### Packaging

- The build toolchain is pinned alongside the dependencies: `setuptools` to an exact version, the `python:3.13-slim` base image by digest, and every GitHub Action to a full commit SHA rather than a moving major tag. The `uv` binary image, previously referenced as `:latest`, is pinned to 0.12.1 by digest. A floating tag can change what a build produces with nobody deciding, which is the same failure the dependency pins address.

## [0.2.0] - 2026-07-25

### Fixed
- A project name that two projects share only *after* a refresh is now reported as an ambiguity error by the completion-tracking tools, instead of escaping as an unhandled exception. They resolve the name, force a refresh if it did not resolve, then resolve again -- and that second attempt is the first point at which the duplicate can appear, because it arrives with the sync.
- A project entry whose ID is missing or is not a string no longer breaks name matching -- it raised `KeyError` on the first and reported a spurious ambiguity on the second. Such an entry cannot be resolved to, so it is simply not a match.
- A task whose `parentId` is present but not a string no longer causes a spurious `protected_task` refusal. Such a parent normalised to the empty string and pooled every affected task under one key in the relation index, which a caller naming a blank or whitespace task ID then inherited -- refused, and told an unrelated task was the reason.
- The protected-task relation guard now returns `outcome: "protection_unverifiable"` when `client.state` is not readable, rather than treating it as "no relations found". Reading an unreadable state as empty is the fail-open answer: it would let a protected task be deleted through a parent nobody named. The resolver, where nothing irreversible hangs on the answer, treats the same state as an empty project list and passes the value through untouched.

### Changed
- With `TICKTICK_MCP_PROTECTED_TASK_IDS` set, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask` now fetch the account once per call rather than twice, whenever that fetch succeeds. If it fails the guard still retries on its own, and proceeds only if the retry lands. The relation guard forced a refresh and the tool immediately forced another, with nothing in between touching the server. TickTick rate-limits, and these are the calls least able to afford a lockout.
- The completion-tracking tools distinguish a project they cannot find from one they cannot confirm. A failed refresh now returns `outcome: "project_list_unverifiable"` and asks for a retry, instead of asserting that a project which may well exist does not.
- The completion-tracking tools now refuse a `project_id` they cannot resolve to a real project, instead of accepting it. That value is the completion database's key, so an unresolved one writes a row no later ID-keyed read can find and no tool can repair.
- `ticktick_mark_completion_processed` now requires an authenticated client. It previously touched only the local completion database and worked during an auth outage or rate-limit window; it now resolves its `project_id` first, and without a client that resolution cannot run. Left ungated, a call made while auth was down would key the completion row by the project *name* - invisible to every later ID-keyed read, and not repairable by any tool. A caller passing an ID rather than a name gains the requirement without needing the resolution, which is the cost of closing that hole.

### Added
- Resolving a project reference now refreshes local state itself, and only when it has a name to match - an ID short-circuits before any sync, so ID callers pay no round-trip. The protected-task relation guard does the same. Previously each of the eight call sites had to remember to sync first, and five of them placed it wrongly or omitted it, which could return an empty result for a real project or let a delete through that a protected subtask should have refused.
- `ticktick_filter_tasks` no longer silently returns an empty list when given a project name; it resolves the name like every other tool, and an ambiguous one returns an error instead of escaping as an unhandled exception.
- `ticktick_create_task` now warns when content is longer than the compact preview window. List tools return compact output by default, so the remainder is invisible there and will not match a keyword search against it. The threshold is read from the same constant the preview uses, so the two cannot drift apart.
- `ticktick_complete_task` now tags every success with `outcome`, not just two of the three paths. A task refetched at status 2 reports `completed`, and a non-recurring task still open afterwards reports `uncertain` rather than being labelled a success the server cannot confirm. Callers can branch on the one field instead of interpreting warning text -- which matters most for `completed_recurring`, since it returns at status 0 and reads as a failure to anything checking status.
- Project **names** are accepted by every tool that takes a project ID (`ticktick_create_task`, `ticktick_get_tasks_from_project`, `ticktick_update_task`, `ticktick_move_task`, `ticktick_delete_tasks`, `ticktick_filter_tasks`, and both completion-tracking tools, where the value is also the completion-DB key so name and ID callers stay on one key), matched case-insensitively after trimming, with `"Inbox"` resolving to the inbox. The change is additive: IDs always win, and a value the server cannot resolve is passed to the API untouched, so nothing that works today changes. The one new error is ambiguity -- two projects sharing a name fails and names both IDs rather than guessing, because picking either files the task somewhere the caller will not think to look.
- `ticktick_create_task` now warns when a title contains a character TickTick parses as a marker rather than text: `#` creates a tag on the account, `@` is read as an assignee, `~` as a duration. Each marker only binds to what follows it, so ordinary titles like "Fix C# build" stay quiet.
- `ticktick_create_task` now warns when a task ends up all-day, whether the caller asked for it or `ticktick-py` inferred it from midnight start/due dates. It has a due date, so the existing dateless warning stays quiet, but TickTick never fires a timed reminder for it -- which reads as a working reminder right up until the day it does not arrive.
- Tasks can be marked off-limits to modification via `TICKTICK_MCP_PROTECTED_TASK_IDS`, a space- or comma-separated list of task IDs. `ticktick_update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask` refuse any call naming one, returning `outcome: "protected_task"` without sending a request that reads or writes the task; reads are unaffected and an unset variable leaves behaviour unchanged. A batch delete containing a protected ID is refused in full rather than partially applied, because a partial delete cannot be undone. Since TickTick propagates delete and move through the whole subtree, the three structural tools also refuse when a protected task is the parent of, or anywhere beneath, a task the caller named; that check forces a refresh first, so a subtask attached from another device cannot slip through a throttled snapshot, and returns `outcome: "protection_unverifiable"` if that refresh fails, rather than deciding on a snapshot it could not update. It costs nothing when no task is protected. IDs are compared ignoring surrounding whitespace, quotes and case, so a padded or recased ID cannot slip past a check the API itself would resolve.

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
- Routable-reopen guidance: `ticktick_update_task` returns `outcome: "needs_project_id"` when the target id is not in local sync state (`get_by_id` returns `{}`, typical for a completed recurring-history occurrence) and no `projectId` was supplied - the projectId-less open-API update would silently no-op, so the tool skips the futile POST and asks for a `projectId` (which lets the reopen succeed) instead of dead-end retry advice.
- Recurring reopen guard: `ticktick_update_task` returns `outcome: "reopen_no_effect"` (an error) when the only substantive change is `status:0` on a recurring task that has already rolled forward - such a "reopen" of the series id changes nothing and does not undo the completion, so it is refused with an explanation instead of reading as success. Updates that also change another field proceed unchanged.

[Unreleased]: https://github.com/partymola/ticktick-mcp/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/partymola/ticktick-mcp/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/partymola/ticktick-mcp/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/partymola/ticktick-mcp/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/partymola/ticktick-mcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/partymola/ticktick-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/partymola/ticktick-mcp/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/partymola/ticktick-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/partymola/ticktick-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/partymola/ticktick-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/partymola/ticktick-mcp/releases/tag/v0.1.0
