# Named stage rather than a bare `COPY --from=<image>`: Dependabot reads FROM
# instructions, so this is what keeps the uv pin under automated updates.
FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv

# Build stage: uv installs from uv.lock, so the image is built from the exact
# resolved set rather than whatever a fresh resolve would pick today.
# git is needed because the ticktick-py fork is a git-source dependency.
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS build

COPY --from=uv /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

# Runtime stage: no git, just the built virtualenv and source.
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5

WORKDIR /app

COPY --from=build /app /app

# Must be set in this stage: an ENV above a later FROM belongs to that stage and
# never reaches the image. The default resolves under the container's own HOME,
# which nothing is told to mount - so the cached OAuth token and v2 session
# token go with the container and a password signon runs on every start, which
# TickTick throttles.
ENV TICKTICK_MCP_DOTENV_DIR=/data
VOLUME ["/data"]

# Ownership proof for the MCP registry (must match server.json name).
LABEL io.modelcontextprotocol.server.name="io.github.partymola/ticktick-mcp"

ENTRYPOINT ["/app/.venv/bin/ticktick-mcp"]
