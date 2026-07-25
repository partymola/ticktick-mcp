# ticktick-mcp

[![CI](https://github.com/partymola/ticktick-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/partymola/ticktick-mcp/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Glama MCP Server](https://glama.ai/mcp/servers/partymola/ticktick-mcp/badges/score.svg)](https://glama.ai/mcp/servers/partymola/ticktick-mcp)

MCP server for TickTick task management. Create, update, complete, move, and filter tasks via the TickTick v2 API, with field-preserving updates, day-of-week date validation, read-after-write verification, and idempotent completion tracking.

Designed for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and other [MCP](https://modelcontextprotocol.io/) clients.

Unofficial. Not affiliated with TickTick Ltd. Built on [`ticktick-py`](https://github.com/partymola/ticktick-py) (MIT).

## Features

- **Full task lifecycle** - create, update, complete, move, subtask, and delete
- **Field-preserving updates** - `ticktick_update_task` re-fetches the task and overlays only the fields you set, so the API never wipes the ones you omit
- **Day-of-week validation** - any call that sets a date must confirm the weekday, catching off-by-one date mistakes before they reach the server
- **Read-after-write verification** - create/update re-read the task and surface `_verification_warnings` when the server echo doesn't match
- **Compact listing** - list tools return a trimmed view by default so large projects stay under the MCP result-size cap (see below)
- **Fresh reads** - read tools re-sync server state on demand, so edits made from the TickTick app on other devices show up without a restart
- **Completion tracking** - mark completed tasks as processed so an agent reviews each one exactly once

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended - see the install note below)
- A TickTick account
- A registered TickTick app for OAuth credentials (free - [developer.ticktick.com](https://developer.ticktick.com/manage))

## Install

```bash
git clone https://github.com/partymola/ticktick-mcp
cd ticktick-mcp
uv sync
```

This creates a `.venv` and installs from `uv.lock`, giving you the console script at `.venv/bin/ticktick-mcp`.

> **Use uv, not plain `pip`.** This server depends on a fork of `ticktick-py` pinned via `[tool.uv.sources]` / `uv.lock`. `uv` honours that pin; a plain `pip install .` silently resolves the bare `ticktick-py` name from PyPI instead, giving you the upstream package without this project's fixes.

## Credentials

TickTick sign-in needs two things: an OAuth app (client ID + secret) and your own account login.

1. Register an app at [developer.ticktick.com](https://developer.ticktick.com/manage). Set the **Redirect URI** to `http://localhost:8080/redirect`. Note the **Client ID** and **Client Secret**.
2. Copy the template and fill it in:

   ```bash
   cp .env.example .env
   ```

   ```
   TICKTICK_CLIENT_ID=your_client_id
   TICKTICK_CLIENT_SECRET=your_client_secret
   TICKTICK_REDIRECT_URI=http://localhost:8080/redirect
   TICKTICK_USERNAME=your_ticktick_email
   TICKTICK_PASSWORD=your_ticktick_password
   ```

There is no separate `auth` step. The server logs in lazily on the first tool call (username/password via `ticktick-py`, plus the OAuth token), then caches the OAuth token next to your `.env` as `.token-oauth` and refreshes it automatically.

The server looks for `.env` in this order: the `--dotenv-dir <path>` argument, then the `TICKTICK_MCP_DOTENV_DIR` environment variable, then `~/.config/ticktick-mcp/`. If no `.env` is found it falls back to the `TICKTICK_*` environment variables directly, which is convenient for container/CI use.

## Privacy and the unofficial API

Your TickTick credentials live only in your local `.env` (or the environment) and are sent only to TickTick's own servers - never to the developer or any third party. The server reads and writes only your own account.

This server uses TickTick's unofficial v2 API (via `ticktick-py`) rather than the official Open API. That is a deliberate choice: the official API has no list-completed-tasks endpoint, no tags, and no cross-project task listing - all of which this server relies on. See [docs/why-not-the-official-api.md](https://github.com/partymola/ticktick-mcp/blob/main/docs/why-not-the-official-api.md) for the full rationale, the risk trade-off, and the triggers that would make us reconsider.

## Register with Claude Code

```bash
claude mcp add -s user ticktick -- /path/to/ticktick-mcp/.venv/bin/ticktick-mcp --dotenv-dir /path/to/config
```

`--dotenv-dir` is optional if your `.env` lives in `~/.config/ticktick-mcp/` or you supply the `TICKTICK_*` variables through the environment.

Then ask Claude things like:
- "What's on my TickTick list for this week?"
- "Add a task to call the dentist on Friday at 9am."
- "Mark the grocery task as done."
- "Move the budget task to the Finance project."

## CLI

```
ticktick-mcp                       Start the MCP server (stdio transport)
ticktick-mcp --dotenv-dir PATH     Directory holding the .env file
ticktick-mcp --version             Print the installed package version
```

The server has no other subcommands - it is the MCP server. All task operations happen through the MCP tools below.

## MCP tools

| Tool | Description |
|------|-------------|
| `ticktick_create_task` | Create a task, preserving date/reminder/priority/timezone fields; warns if no due date is set (no reminder would fire) |
| `ticktick_update_task` | Update a task by overlaying only the fields you set onto the current server object (omitted fields are never wiped) |
| `ticktick_complete_task` | Mark a task complete and re-verify; distinguishes a recurring task rolling forward from a normal completion |
| `ticktick_delete_tasks` | Delete one or more tasks by ID |
| `ticktick_move_task` | Move a task into a different project |
| `ticktick_make_subtask` | Nest one task as a subtask of another in the same project |
| `ticktick_get_tasks_from_project` | List every open task in a project (compact or full) |
| `ticktick_filter_tasks` | Find tasks by any mix of project, priority, tag, status, and due/completion-date window |
| `ticktick_get_by_id` | Look up any task, project, or tag by its full ID |
| `ticktick_get_all` | Dump all projects or all tags from local state |
| `ticktick_sync` | Force an immediate refresh of local state from the server |
| `ticktick_get_unprocessed_completions` | List recently completed tasks in a project not yet marked processed |
| `ticktick_mark_completion_processed` | Record that a completed task has been reviewed, excluding it from future checks |
| `ticktick_convert_datetime_to_ticktick_format` | Convert an ISO 8601 datetime + IANA timezone to TickTick's wire format |

## Listing tasks: compact by default

The list-returning tools - `ticktick_get_tasks_from_project` and `ticktick_filter_tasks` - default to `detail="compact"`. Compact output keeps the browsing-relevant fields (`id`, `projectId`, `title`, `dueDate`, `startDate`, `priority`, `status`, `isAllDay`, `timeZone`, `tags`) plus a `contentPreview` (the first ~200 chars of `content`), and drops the heavy `content`/`desc`/checklist `items` blobs and bulky sync metadata. This keeps large projects under the MCP result-size cap so the client does not have to spill the result to disk. Keyword search still works against `title` and `contentPreview`.

- Need the full objects? Pass `detail="full"`.
- Need one task's full content? Use `ticktick_get_by_id`.
- **Editing a task:** fetch the full object with `ticktick_get_by_id` first, then send every field back via `ticktick_update_task`. The TickTick API wipes any field omitted from an update, so compact output must never feed an update.

If a compact result would still exceed the size budget, the soonest-due tasks are returned and a final `_truncation_note` element reports how many were omitted - nothing is dropped silently. Reach the rest with a narrower `ticktick_filter_tasks` query, `detail="full"`, or `ticktick_get_by_id`.

## Freshness: reads stay current

The TickTick account can be edited from the app on other devices while the server runs. To keep reads from going stale, the read tools re-sync server state on demand, throttled to at most once per window (default 15s, override with `TICKTICK_MCP_SYNC_TTL_SECONDS`). A change made elsewhere becomes visible within that window; call `ticktick_sync` to force an immediate refresh and get the current task/project counts. If a sync fails, the last-known state is served rather than erroring.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TICKTICK_MCP_DOTENV_DIR` | `~/.config/ticktick-mcp/` | Directory holding the `.env` file (the `--dotenv-dir` argument takes precedence) |
| `TICKTICK_MCP_SYNC_TTL_SECONDS` | `15` | Minimum seconds between on-demand read re-syncs |
| `TICKTICK_MCP_INIT_RETRY_SECONDS` | `60` | Cooldown before retrying client login after a failed first connection |
| `TICKTICK_MCP_RATELIMIT_RETRY_SECONDS` | `300` | Cooldown before retrying login after a rate-limit (HTTP 429); longer than the init cooldown because a 429 clears slowly and each retry prolongs it |
| `TICKTICK_MCP_PROTECTED_TASK_IDS` | unset | Task IDs an agent must never modify, separated by spaces or commas. Every mutating tool refuses before sending anything; reads are unaffected. Unset means no protection. |

### Protecting tasks from modification

Some tasks should never be changed by an agent, whatever it is asked to do. List their IDs in `TICKTICK_MCP_PROTECTED_TASK_IDS`:

```bash
TICKTICK_MCP_PROTECTED_TASK_IDS="60ca9dbc8f08516d9dd56324,60ca9dbc8f08516d9dd56325"
```

`ticktick_update_task`, `ticktick_complete_task`, `ticktick_delete_tasks`, `ticktick_move_task` and `ticktick_make_subtask` then refuse any call naming a protected task, returning `outcome: "protected_task"`. No request that reads or writes the task is sent. A batch delete containing a protected ID is refused in full rather than partially applied, since a partial delete cannot be undone.

Because TickTick propagates delete and move through subtasks, `delete`, `move` and `make_subtask` also refuse when a protected task is the parent or the subtask of a task you named. That check reads already-synced local state, so it costs no extra request; it cannot see relations that state does not hold. IDs are matched ignoring surrounding whitespace, quotes and case. Reading protected tasks always works.

Credentials (`TICKTICK_CLIENT_ID`, `TICKTICK_CLIENT_SECRET`, `TICKTICK_REDIRECT_URI`, `TICKTICK_USERNAME`, `TICKTICK_PASSWORD`) are read from the `.env` file or, if absent, directly from the environment.

## Data safety

A pre-commit hook (`scripts/check-no-data.sh`) blocks accidentally committing databases, credentials, and large files - `*.db` and backup variants, anything under `config/*.json` / `config/*.env` (except `*.example.*`), and files over 100KB (except `uv.lock`). Install it after cloning:

```bash
ln -sf ../../scripts/check-no-data.sh .git/hooks/pre-commit
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, the test workflow, and the pre-commit hook. Changes are tracked in [CHANGELOG.md](CHANGELOG.md).

## License

[GPL-3.0-or-later](LICENSE)
