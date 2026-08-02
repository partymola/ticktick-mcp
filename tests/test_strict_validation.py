"""Verify that the MCP server rejects unknown kwargs instead of silently
dropping them.

The conftest replaces ticktick_mcp.mcp_instance with a fake module, so these
tests bypass that mock and import the real instance for its side effect on
`ArgModelBase`.

The strictness is bought by patching a private `mcp` internal, so what needs
pinning is that unknown kwargs still *fail*, not that a config key still holds
a value. If `func_metadata` ever stops building its models on `ArgModelBase`,
the patch keeps applying cleanly to an object nothing consults and every tool
silently accepts typo'd arguments again.
"""

import sys

import pytest
from pydantic import ValidationError


def test_arg_model_base_rejects_extras():
    """The mcp_instance patch must set extra='forbid' on ArgModelBase, so
    that all subsequent tool-arg models reject unknown fields."""
    # Drop any cached fake module from conftest so we re-import the real one.
    sys.modules.pop("ticktick_mcp.mcp_instance", None)

    import ticktick_mcp.mcp_instance  # noqa: F401, I001 - import for side effect
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

    assert ArgModelBase.model_config.get("extra") == "forbid"


def test_dynamic_arg_model_rejects_extras():
    """A model built from a fake tool signature should reject extras."""
    sys.modules.pop("ticktick_mcp.mcp_instance", None)
    import ticktick_mcp.mcp_instance  # noqa: F401, I001
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata

    async def fake_tool(title: str, project_id: str | None = None) -> str:
        return "ok"

    meta = func_metadata(fake_tool, structured_output=False)
    # Known kwargs validate.
    meta.arg_model.model_validate({"title": "t", "project_id": "p"})
    # Unknown kwarg (camelCase variant) must error.
    with pytest.raises(ValidationError):
        meta.arg_model.model_validate({"title": "t", "projectId": "p"})


def test_registered_tool_rejects_unknown_kwarg():
    """End-to-end: a tool registered on a real server, invoked with a typo'd
    kwarg, must raise rather than fall through to the parameter's default.

    This is the one that still fails if the patched base stops being the base
    the models are built on - the two above assert against the same internal
    the patch targets, so they would agree with each other and be wrong.
    """
    import asyncio

    sys.modules.pop("ticktick_mcp.mcp_instance", None)
    import ticktick_mcp.mcp_instance  # noqa: F401, I001
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("strictness-probe")

    @server.tool()
    def echo(wanted: str = "fallthrough") -> str:
        return wanted

    with pytest.raises(Exception, match="wnated|[Ee]xtra"):
        asyncio.run(server.call_tool("echo", {"wnated": "typo"}))
