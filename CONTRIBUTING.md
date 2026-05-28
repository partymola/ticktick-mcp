# Contributing to ticktick-mcp

Thanks for your interest in contributing. This is a community MCP server for the TickTick v2 API.

## Getting started

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A [TickTick developer app](https://developer.ticktick.com/) with `CLIENT_ID` and `CLIENT_SECRET`, plus a TickTick account

### Set up the dev environment

```bash
git clone https://github.com/partymola/ticktick-mcp
cd ticktick-mcp
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
```

### Install the pre-commit hook

The repo ships with `scripts/check-no-data.sh`, which blocks commits that contain databases, tokens, or other secrets:

```bash
ln -sf ../../scripts/check-no-data.sh .git/hooks/pre-commit
```

Please install it before your first commit.

### Run the test suite

```bash
.venv/bin/python -m pytest tests/ -v
```

Tests are fully offline — no real API calls, no real tokens. Fixtures use mocked clients.

### Run lint and formatting checks

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
```

## Making changes

- **Open an issue first** for non-trivial changes (new tools, schema changes, breaking changes). Small fixes (typos, bug fixes, docs) can go straight to a PR.
- Keep PRs small and focused.
- Add tests for new behaviour.
- Update `CHANGELOG.md` under `[Unreleased]`.

## Data safety

- Never commit `.env`, `config/*.env`, OAuth tokens, completion-tracking databases, or anything matching the patterns in `scripts/check-no-data.sh`.
- Test fixtures must use mocked clients with synthetic data — no real task content, project IDs, or account identifiers from a live TickTick account.
