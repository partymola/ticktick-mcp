"""Shared FastMCP instance for the TickTick MCP server.

Lives in its own module so every tool module can import the same singleton
without pulling in heavyweight dependencies (config, OAuth client, etc.).
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

# Pydantic v2's default extra-field policy is "ignore", which means FastMCP
# silently drops unknown kwargs from a tool invocation - a typo on a kwarg
# name (e.g. snake_case vs camelCase) returns no error and the param falls
# through to its default value. Force strict validation so callers see a
# real error instead of a silent fallthrough.
ArgModelBase.model_config["extra"] = "forbid"

mcp = FastMCP("ticktick-server")
