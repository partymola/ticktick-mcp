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

## Test conventions

- Async tools are tested via `asyncio.run()` wrapper.
- Mock `TickTickClientSingleton.get_client()` for all tests; never hit the live API.
- Group tests by behaviour class in `tests/test_*.py`.
