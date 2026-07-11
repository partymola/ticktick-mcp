# Build stage: uv installs faithfully from uv.lock, which pins the patched
# ticktick-py fork. A plain `pip install .` would silently pull the upstream
# ticktick-py from PyPI, whose login is broken - so the container builds with uv.
# git is needed because the fork is a git-source dependency.
FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

# Runtime stage: no git, just the built virtualenv and source.
FROM python:3.13-slim

WORKDIR /app

COPY --from=build /app /app

# Ownership proof for the MCP registry (must match server.json name).
LABEL io.modelcontextprotocol.server.name="io.github.partymola/ticktick-mcp"

ENTRYPOINT ["/app/.venv/bin/ticktick-mcp"]
