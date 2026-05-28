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
``parse_known_args`` so a host process's own flags are tolerated) and loads
the ``.env`` file when one is present. If the directory or ``.env`` file is
missing, the server still starts and relies on credentials passed directly as
environment variables -- this lets it boot in container/CI environments (e.g.
registry tool-introspection) that inject credentials as env vars rather than
mounting a ``.env`` file. Tests must mock this module before importing any of
the tool modules; see ``tests/conftest.py``.
"""

import argparse
import logging
import os
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
    """Resolve the config directory and load credentials from it.

    Loads a ``.env`` file from the resolved directory when present. When the
    directory or file is absent the server does not abort: it falls back to
    credentials supplied directly via environment variables
    (``TICKTICK_CLIENT_ID`` etc.), which is how container/CI environments
    typically inject them. The directory is created if missing so the OAuth
    token cache and completion-tracking DB have somewhere to live.
    """
    dotenv_dir = _resolve_dotenv_dir()

    env_file = dotenv_dir / ".env"
    if env_file.is_file():
        load_dotenv(env_file)
        logger.info("Loaded TickTick MCP credentials from %s", env_file)
    else:
        logger.warning(
            "ticktick-mcp: no .env file at %s; relying on environment "
            "variables for credentials (TICKTICK_CLIENT_ID, "
            "TICKTICK_CLIENT_SECRET, TICKTICK_USERNAME, TICKTICK_PASSWORD).",
            env_file,
        )
        try:
            dotenv_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "ticktick-mcp: could not create config dir %s: %s",
                dotenv_dir,
                exc,
            )

    return dotenv_dir


dotenv_dir_path: Path = _load_env()

CLIENT_ID: str = os.environ.get("TICKTICK_CLIENT_ID", "")
CLIENT_SECRET: str = os.environ.get("TICKTICK_CLIENT_SECRET", "")
REDIRECT_URI: str = os.environ.get("TICKTICK_REDIRECT_URI", "http://localhost:8080/redirect")
USERNAME: str = os.environ.get("TICKTICK_USERNAME", "")
PASSWORD: str = os.environ.get("TICKTICK_PASSWORD", "")
