# Why this server uses the unofficial TickTick API

**Decision (2026-07-12): keep using `ticktick-py` (TickTick's unofficial v2 API); do not migrate to the official TickTick Open API.**

## Background

`ticktick-mcp` talks to TickTick through the [`ticktick-py`](https://github.com/partymola/ticktick-py) library, which uses TickTick's **unofficial v2 API** (the same one the web client uses). On connect it performs a username/password login plus a registered OAuth app, and pulls the full account - all open tasks, projects, and tags - into local state in one sync. Most of the list and filter tools read from that synced state; completed tasks are fetched when asked for. Create, update and complete are the exception, posting to the Open API with that OAuth token.

The alternative is the **official TickTick Open API** (`https://api.ticktick.com/open/v1`, OAuth 2.0, scopes `tasks:read`/`tasks:write`), which needs no `ticktick-py` and never stores the user's password.

## Why not migrate

The Open API lists completed tasks and tags, and it filters across projects. Two things this server does still need v2.

- **Removing a reminder.** The Open API keeps a task's reminders while the task keeps its date. Only v2 clears them.
- **Reading every open task at once.** v2 returns every open task, project and tag in one sync. The id lookups and the protected-task guard read from that copy. The Open API needs a call per project for the same, and its cross-project filter stops at 200 tasks.

Moving would end the need for a stored password and the headless login. It would also lose reminder removal and turn each full read into a call per project. That trade is not yet worth making while the fork's login works.

## Risk being accepted, and its mitigations

The real risk of the unofficial path is that TickTick changes or gates the headless username/password login (adds captcha/2FA, or reshapes v2). Note that v2 itself is the web client's own API, so it won't simply disappear; what could plausibly break is specifically the headless login (which has broken once upstream - hence the fork).

Mitigations already in place:
- The `ticktick-py` dependency is a **pinned patched fork** (`uv.lock`), and the container image builds with `uv` so it uses the fork rather than the broken upstream.
- Login is a **per-process singleton** - it happens once per server start, not per request, so the exposure window is small; the OAuth token is cached at `.token-oauth`, and `ticktick-mcp auth` renews it when it expires.
- A failed login is **retried after a cooldown** rather than cached for the process lifetime.
- The twice-daily task backup exercises the same login, acting as a canary (its failure should be surfaced - see the ops runbook).

## Reopen triggers

Revisit this decision if **either**:

1. The headless v2 login breaks and cannot be re-fixed in the fork within a few days; **or**
2. The Open API gains reminder removal.

## Contingency if v2 login dies permanently

Move this server onto the Open API. The completion record stays, because it lives in this server. The protected-task guard stays too, but its subtask check would read every project on each delete or move. Removing reminders would stop working.
