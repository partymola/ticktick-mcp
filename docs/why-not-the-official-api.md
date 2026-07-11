# Why this server uses the unofficial TickTick API

**Decision (2026-07-12): keep using `ticktick-py` (TickTick's unofficial v2 API); do not migrate to the official TickTick Open API.**

## Background

`ticktick-mcp` talks to TickTick through the [`ticktick-py`](https://github.com/partymola/ticktick-py) library, which uses TickTick's **unofficial v2 API** (the same one the web client uses). On connect it performs a username/password login plus a registered OAuth app, and pulls the full account - all tasks, projects, and tags - into local state in one sync. Most of the list/filter/completion tools read from that synced state.

The alternative is the **official TickTick Open API** (`https://api.ticktick.com/open/v1`, OAuth 2.0, scopes `tasks:read`/`tasks:write`), which needs no `ticktick-py` and never stores the user's password.

## Why not migrate

The official API is deliberately minimal. It can create/get/update/complete/delete a task (every operation requires a known `projectId`) and do project CRUD. It **cannot**:

- **List completed tasks.** There is no completion-history endpoint. This alone is decisive: it makes `ticktick_get_unprocessed_completions` + `ticktick_mark_completion_processed` - the "process each completed task exactly once" workflow that automation is built around - impossible to reproduce.
- **Tags** - no endpoint at all (read, assign, or filter).
- **List tasks across projects** - every read is scoped to one project, so any cross-project list or filter becomes an N+1 fetch (list projects, then fetch each project's tasks) instead of one sync.
- **Bare-ID lookup** of a task/project/tag without its `projectId`; **convert an existing task into a subtask**; batch operations; habits.

Migrating would trade those features - which are the point of this server - for the sole gains of OAuth robustness and not storing a password. That trade isn't worth it while the fork is maintained.

## Risk being accepted, and its mitigations

The real risk of the unofficial path is that TickTick changes or gates the headless username/password login (adds captcha/2FA, or reshapes v2). Note that v2 itself is the web client's own API, so it won't simply disappear; what could plausibly break is specifically the headless login (which has broken once upstream - hence the fork).

Mitigations already in place:
- The `ticktick-py` dependency is a **pinned patched fork** (`uv.lock`), and the container image builds with `uv` so it uses the fork rather than the broken upstream.
- Login is a **per-process singleton** - it happens once per server start, not per request, so the exposure window is small; the OAuth token is cached at `.token-oauth` and auto-refreshed.
- A failed login is **retried after a cooldown** rather than cached for the process lifetime.
- The twice-daily task backup exercises the same login, acting as a canary (its failure should be surfaced - see the ops runbook).

## Reopen triggers

Revisit this decision if **either**:

1. The headless v2 login breaks and cannot be re-fixed in the fork within a few days; **or**
2. TickTick ships a list-completed-tasks endpoint **and** a tags endpoint on the official API.

## Contingency if v2 login dies permanently

Fall back to the official OAuth API for basic task/project CRUD, with completions-history and tags features marked unavailable; or extract a session token from the web client as a stopgap. The fallback is a bounded, later-payable cost - which is exactly why it isn't worth paying pre-emptively now.
