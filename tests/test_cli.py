"""Tests for the ticktick-mcp command line.

Nothing here may reach a real browser, a real socket or the real credential
files. `auth` exists precisely to run the interactive OAuth step, so every
one of those is replaced with something that raises if touched, rather than
relied on being unreachable: the path under test is the one that reaches
them.
"""

import sys
from importlib.metadata import version
from unittest.mock import patch

import pytest

from ticktick_mcp import cli


@pytest.fixture(autouse=True)
def _block_every_interactive_path(monkeypatch):
    import webbrowser

    def refuse(*args, **kwargs):
        raise AssertionError("an interactive or network path was reached")

    monkeypatch.setattr(webbrowser, "open", refuse)
    monkeypatch.setattr("builtins.input", refuse)


def test_version_prints_and_returns_without_loading_config(capsys):
    with patch.object(sys, "argv", ["ticktick-mcp", "--version"]):
        cli.main()
    assert capsys.readouterr().out.strip() == f"ticktick-mcp {version('ticktick-mcp')}"


def test_auth_builds_the_client_once_and_exits_zero(capsys):
    """The whole point: the browser step happens here, not in the server."""
    with (
        patch.object(sys, "argv", ["ticktick-mcp", "auth"]),
        patch("ticktick_mcp.client.TickTickClientSingleton.get_client", return_value=object()),
        patch.object(cli, "_version_text", side_effect=AssertionError("wrong branch")),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 0
    assert "Authorised" in capsys.readouterr().out


def test_auth_reports_the_failure_and_exits_nonzero(capsys):
    with (
        patch.object(sys, "argv", ["ticktick-mcp", "auth"]),
        patch("ticktick_mcp.client.TickTickClientSingleton.get_client", return_value=None),
        patch(
            "ticktick_mcp.client.TickTickClientSingleton.last_error",
            return_value="credentials rejected",
        ),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 1
    assert "credentials rejected" in capsys.readouterr().err


def test_auth_never_starts_the_mcp_server():
    """It must exit, not fall through into mcp.run."""
    with (
        patch.object(sys, "argv", ["ticktick-mcp", "auth"]),
        patch("ticktick_mcp.client.TickTickClientSingleton.get_client", return_value=object()),
        patch("ticktick_mcp.mcp_instance.mcp.run", side_effect=AssertionError("server started")),
    ):
        with pytest.raises(SystemExit):
            cli.main()


def test_the_readme_command_is_the_one_the_cli_implements():
    """The previous README told users to run the bare server and wait.

    That command loads config and starts the stdio server; it never reaches
    the OAuth construction, so it appeared to succeed and authorised nothing.
    """
    import pathlib

    readme = (pathlib.Path(cli.__file__).parents[2] / "README.md").read_text()
    assert "ticktick-mcp auth" in readme
    assert "--dotenv-dir ~/.config/ticktick-mcp\n```" not in readme
