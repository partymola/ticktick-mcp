"""Shared MCP server instance for the TickTick MCP server.

Lives in its own module so every tool module can import the same singleton
without pulling in heavyweight dependencies (config, OAuth client, etc.).
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

# Pydantic v2's default extra-field policy is "ignore", which means the server
# silently drops unknown kwargs from a tool invocation - a typo on a kwarg
# name (e.g. snake_case vs camelCase) returns no error and the param falls
# through to its default value. Force strict validation so callers see a
# real error instead of a silent fallthrough.
#
# This reaches into a private module, so it is not covered by the API
# compatibility the public surface promises: it holds only while
# `func_metadata` keeps building tool-argument models on `ArgModelBase`. The
# import breaking is the harmless failure. The dangerous one is silent - the
# import still resolving while the models are built on some other base, which
# restores the exact silent-fallthrough behaviour this defends against.
# tests/test_strict_validation.py checks the behaviour, not the setting, for
# that reason; keep it that way.
ArgModelBase.model_config["extra"] = "forbid"

mcp = MCPServer("ticktick-server")
