"""TickTick MCP server entry point.

Usage:
    ticktick-mcp                                 Start the MCP server (stdio transport)
    ticktick-mcp auth                            Authorise once, at a terminal
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


def _run_auth() -> int:
    """Build the client once, so the OAuth step happens at a terminal.

    ticktick-py has no refresh token: on first use and again at expiry it
    opens a browser and calls input(). Inside the server that input() reads
    the JSON-RPC channel, so it has to happen here instead.
    """
    from .client import TickTickClientSingleton

    print("Authorising with TickTick. A browser will open; paste back the URL you land on.")
    client = TickTickClientSingleton.get_client()
    if client is None:
        print(f"Authorisation failed: {TickTickClientSingleton.last_error()}", file=sys.stderr)
        return 1
    print("Authorised. The token is cached; start the server normally from now on.")
    return 0


def main() -> None:
    # Importing config triggers .env loading; doing it here means
    # `--version` still works without a configured .env file.
    if "--version" in sys.argv:
        print(_version_text())
        return

    if "auth" in sys.argv[1:]:
        from . import config  # noqa: F401

        sys.exit(_run_auth())

    # Loading config has side effects (parses sys.argv for --dotenv-dir and
    # reads the .env file if present; falls back to env-var credentials when
    # it is absent).
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
