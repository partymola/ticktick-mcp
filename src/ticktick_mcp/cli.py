"""TickTick MCP server entry point.

Usage:
    ticktick-mcp                                 Start the MCP server (stdio transport)
    ticktick-mcp --version                       Print the installed package version
    ticktick-mcp --dotenv-dir /path/to/config    Override the .env directory

The ``--dotenv-dir`` flag is consumed by ``ticktick_mcp.config`` at import
time, so it works regardless of which subcommand (if any) is supplied.
"""

import logging
import sys
from importlib.metadata import version

# Log to stderr so we never poison the stdio JSON-RPC channel.
logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
    stream=sys.stderr,
)


def _version_text() -> str:
    return f"ticktick-mcp {version('ticktick-mcp')}"


def main() -> None:
    # Importing config triggers .env loading; doing it here means
    # `--version` still works without a configured .env file.
    if "--version" in sys.argv:
        print(_version_text())
        return

    # Loading config has side effects (parses sys.argv for --dotenv-dir,
    # reads the .env file, exits on missing config).
    from . import config  # noqa: F401

    from .mcp_instance import mcp
    # Tool modules register themselves with `mcp` at import time.
    from .tools import (  # noqa: F401
        completion_tools,
        conversion_tools,
        filter_tools,
        task_tools,
    )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
