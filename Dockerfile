# Build stage: uv installs from uv.lock, so the image is built from the exact
# resolved set rather than whatever a fresh resolve would pick today.
# git is needed because the ticktick-py fork is a git-source dependency.
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

# Runtime stage: no git, just the built virtualenv and source.
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

WORKDIR /app

COPY --from=build /app /app

# Ownership proof for the MCP registry (must match server.json name).
LABEL io.modelcontextprotocol.server.name="io.github.partymola/ticktick-mcp"

ENTRYPOINT ["/app/.venv/bin/ticktick-mcp"]
