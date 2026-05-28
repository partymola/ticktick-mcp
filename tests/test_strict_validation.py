"""Verify that the MCP server rejects unknown kwargs instead of silently
dropping them.

The conftest replaces ticktick_mcp.mcp_instance with a fake module, so this
test bypasses that mock and imports the real instance to inspect the
underlying Pydantic ArgModelBase config.
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
    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

    assert ArgModelBase.model_config.get("extra") == "forbid"


def test_dynamic_arg_model_rejects_extras():
    """A model built from a fake tool signature should reject extras."""
    sys.modules.pop("ticktick_mcp.mcp_instance", None)
    import ticktick_mcp.mcp_instance  # noqa: F401, I001
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    async def fake_tool(title: str, project_id: str | None = None) -> str:
        return "ok"

    meta = func_metadata(fake_tool, structured_output=False)
    # Known kwargs validate.
    meta.arg_model.model_validate({"title": "t", "project_id": "p"})
    # Unknown kwarg (camelCase variant) must error.
    with pytest.raises(ValidationError):
        meta.arg_model.model_validate({"title": "t", "projectId": "p"})
