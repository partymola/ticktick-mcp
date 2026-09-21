"""Conftest to handle module-level side effects during import.

The ticktick_mcp.config module parses sys.argv and loads a .env file at import
time, which breaks pytest. We patch sys.argv and the config module before any
test module imports task_tools.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Patch sys.argv before any ticktick_mcp module is imported
sys.argv = ["pytest", "--dotenv-dir", "/tmp/ticktick-mcp-test"]

# Pre-create a fake config module so that importing ticktick_mcp modules
# doesn't trigger the real config (which calls parse_args and sys.exit)
fake_config = types.ModuleType("ticktick_mcp.config")
fake_config.CLIENT_ID = "fake_client_id"
fake_config.CLIENT_SECRET = "fake_client_secret"
fake_config.REDIRECT_URI = "http://localhost:8080"
fake_config.USERNAME = "fake_user"
fake_config.PASSWORD = "fake_pass"
fake_config.dotenv_dir_path = Path("/tmp/ticktick-mcp-test")
sys.modules["ticktick_mcp.config"] = fake_config

# Also mock the ticktick library's OAuth2 and TickTickClient to prevent
# any real authentication attempts
fake_ticktick_oauth2 = types.ModuleType("ticktick.oauth2")
fake_ticktick_oauth2.OAuth2 = MagicMock()
sys.modules["ticktick.oauth2"] = fake_ticktick_oauth2

fake_ticktick_api = types.ModuleType("ticktick.api")
fake_ticktick_api.TickTickClient = MagicMock()
sys.modules["ticktick.api"] = fake_ticktick_api

# Mock the MCP server instance to prevent actual server startup
fake_mcp_module = types.ModuleType("ticktick_mcp.mcp_instance")
mock_mcp = MagicMock()
# mcp.tool() should return a decorator that is a no-op (returns the function as-is)
mock_mcp.tool = MagicMock(side_effect=lambda **kwargs: lambda f: f)
# Also support mcp.tool() with no kwargs
mock_mcp.tool.side_effect = None
mock_mcp.tool.return_value = lambda f: f
fake_mcp_module.mcp = mock_mcp
sys.modules["ticktick_mcp.mcp_instance"] = fake_mcp_module

# Mock the require_ticktick_client decorator to be a no-op
import ticktick_mcp.helpers as helpers_module  # noqa: E402

# Stash the real decorator before clobbering it. test_helpers.py needs it to
# test the genuine implementation; without this it had to importlib.reload the
# module, which rebinds every other name too -- including ToolLogicError, so an
# `except ToolLogicError` elsewhere stopped matching and the suite became
# order-dependent.
helpers_module._original_require_ticktick_client = helpers_module.require_ticktick_client
helpers_module.require_ticktick_client = lambda f: f

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Point the completion database at a temp directory for every test.

    Autouse and global rather than local to the completion-tracking tests:
    completing a task records the completion, so any test that completes one
    would otherwise write to the configured directory, and the tool swallows a
    write failure by design, so it would pass either way.
    """
    import ticktick_mcp.completion_db as db_module

    original = db_module._DB_PATH
    db_module._DB_PATH = tmp_path / "completion_tracking.db"
    # Created empty rather than absent, so a test asserting that nothing was
    # recorded reads an empty table instead of failing on a missing one.
    db_module.init_db()
    yield db_module._DB_PATH
    db_module._DB_PATH = original


@pytest.fixture(autouse=True)
def _reset_freshness_state():
    """Clear the module-level sync throttle before every test.

    ``ensure_fresh`` keeps process-wide timestamps; without a reset the
    throttle would leak across test cases and suppress the sync a test
    expects (or vice versa).
    """
    import ticktick_mcp.freshness as _freshness

    _freshness.reset_freshness_state()
    yield
    _freshness.reset_freshness_state()
