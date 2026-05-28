"""Verify the config loader's startup behaviour.

``conftest.py`` replaces ``ticktick_mcp.config`` with a fake module (its
import-time side effects break pytest), so these tests load the real
``config.py`` from its file path under a throwaway module name. That runs the
module body -- including ``_load_env()`` -- under controlled ``sys.argv`` and
environment, without disturbing the cached fake other tests rely on.

Covers the regression where a missing dotenv directory/.env file aborted the
process via ``sys.exit``, preventing the server from booting in environments
that inject credentials as environment variables (e.g. registry
tool-introspection).
"""

import importlib.util
import sys
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "ticktick_mcp" / "config.py"

_TICKTICK_ENV_VARS = (
    "TICKTICK_CLIENT_ID",
    "TICKTICK_CLIENT_SECRET",
    "TICKTICK_REDIRECT_URI",
    "TICKTICK_USERNAME",
    "TICKTICK_PASSWORD",
)


def _load_real_config(monkeypatch, dotenv_dir: Path):
    """Execute the real config module body with ``--dotenv-dir`` set."""
    monkeypatch.setattr(sys, "argv", ["pytest", "--dotenv-dir", str(dotenv_dir)])
    spec = importlib.util.spec_from_file_location("ticktick_mcp._config_under_test", _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_starts_without_env_file_using_env_vars(tmp_path, monkeypatch):
    """A missing dotenv dir must not abort: credentials come from env vars and
    the directory is created for the token cache / completion DB."""
    missing_dir = tmp_path / "no-such-config-dir"
    assert not missing_dir.exists()

    for name in _TICKTICK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICKTICK_CLIENT_ID", "env_client_id")
    monkeypatch.setenv("TICKTICK_CLIENT_SECRET", "env_client_secret")
    monkeypatch.setenv("TICKTICK_USERNAME", "env_user")
    monkeypatch.setenv("TICKTICK_PASSWORD", "env_pass")

    config = _load_real_config(monkeypatch, missing_dir)

    assert config.dotenv_dir_path == missing_dir
    assert missing_dir.is_dir()  # created by the fallback path
    assert config.CLIENT_ID == "env_client_id"
    assert config.CLIENT_SECRET == "env_client_secret"
    assert config.USERNAME == "env_user"
    assert config.PASSWORD == "env_pass"


def test_loads_env_file_when_present(tmp_path, monkeypatch):
    """When a .env file exists its values populate the credential vars."""
    for name in _TICKTICK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "TICKTICK_CLIENT_ID=file_client_id\n"
        "TICKTICK_CLIENT_SECRET=file_client_secret\n"
        "TICKTICK_USERNAME=file_user\n"
        "TICKTICK_PASSWORD=file_pass\n"
    )

    config = _load_real_config(monkeypatch, tmp_path)

    assert config.dotenv_dir_path == tmp_path
    assert config.CLIENT_ID == "file_client_id"
    assert config.CLIENT_SECRET == "file_client_secret"
    assert config.USERNAME == "file_user"
    assert config.PASSWORD == "file_pass"
