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

## Releases (maintainers)

1. Bump `version` in `pyproject.toml` and turn the `[Unreleased]` CHANGELOG heading into `## [X.Y.Z] - YYYY-MM-DD`, adding the compare link at the foot of the file.
2. Push to `main` and wait for CI to pass on that commit.
3. Tag it `vX.Y.Z` and push the tag by name.
4. Create the GitHub Release.

Step 4 is what publishes: `publish-registry.yml` runs on `release: published`, not on the tag push, so the tag on its own ships nothing. It builds the `Dockerfile`, pushes `ghcr.io/partymola/ticktick-mcp:vX.Y.Z` and `:latest`, and publishes to the MCP registry. Because the Release event is what builds the image, do not create it until CI is green on the tagged commit.

**Do not hand-edit `server.json`'s `version` or `packages[0].identifier`.** The workflow rewrites both from the tag before publishing, so the values committed to the repo are deliberately left behind and are not a bug. To see what actually published, query the registry rather than reading the file:

```bash
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.partymola/ticktick-mcp"
```

`--version` reads the installed package metadata, so it follows `pyproject.toml`; the root `version` in `uv.lock` is metadata only and does not affect what the container reports.

## Data safety

- Never commit `.env`, `config/*.env`, OAuth tokens, completion-tracking databases, or anything matching the patterns in `scripts/check-no-data.sh`.
- Test fixtures must use mocked clients with synthetic data — no real task content, project IDs, or account identifiers from a live TickTick account.
