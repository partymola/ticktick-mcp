"""Configuration loader for the TickTick MCP server.

Reads TickTick OAuth credentials from a ``.env`` file in a configurable
directory. The directory is resolved in this priority order:

1. ``--dotenv-dir <path>`` command-line argument
2. ``TICKTICK_MCP_DOTENV_DIR`` environment variable
3. ``~/.config/ticktick-mcp`` (default)

Module attributes (exported):

- ``CLIENT_ID``, ``CLIENT_SECRET``, ``REDIRECT_URI``, ``USERNAME``, ``PASSWORD``
- ``dotenv_dir_path`` -- ``pathlib.Path`` of the resolved directory.

Importing this module has side effects: it parses ``sys.argv`` (with
``parse_known_args`` so a host process's own flags are tolerated), loads the
``.env`` file, and exits the process via ``sys.exit`` if either the directory
or the ``.env`` file is missing. Tests must mock this module before importing
any of the tool modules; see ``tests/conftest.py``.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _resolve_dotenv_dir() -> Path:
    """Pick the dotenv directory using CLI > env var > default."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--dotenv-dir",
        dest="dotenv_dir",
        default=None,
        help="Directory containing the TickTick MCP .env file.",
    )
    args, _ = parser.parse_known_args()

    if args.dotenv_dir:
        return Path(args.dotenv_dir).expanduser()

    env_dir = os.environ.get("TICKTICK_MCP_DOTENV_DIR")
    if env_dir:
        return Path(env_dir).expanduser()

    return Path.home() / ".config" / "ticktick-mcp"


def _load_env() -> Path:
    """Load the .env file and return the directory it lives in."""
    dotenv_dir = _resolve_dotenv_dir()

    if not dotenv_dir.is_dir():
        print(
            f"ticktick-mcp: dotenv directory not found: {dotenv_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    env_file = dotenv_dir / ".env"
    if not env_file.is_file():
        print(
            f"ticktick-mcp: .env file not found at {env_file}. "
            "Copy .env.example to that location and fill in your credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    load_dotenv(env_file)
    logger.info("Loaded TickTick MCP credentials from %s", env_file)
    return dotenv_dir


dotenv_dir_path: Path = _load_env()

CLIENT_ID: str = os.environ.get("TICKTICK_CLIENT_ID", "")
CLIENT_SECRET: str = os.environ.get("TICKTICK_CLIENT_SECRET", "")
REDIRECT_URI: str = os.environ.get("TICKTICK_REDIRECT_URI", "http://localhost:8080/redirect")
USERNAME: str = os.environ.get("TICKTICK_USERNAME", "")
PASSWORD: str = os.environ.get("TICKTICK_PASSWORD", "")
