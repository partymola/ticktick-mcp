"""Shared FastMCP instance for the TickTick MCP server.

Lives in its own module so every tool module can import the same singleton
without pulling in heavyweight dependencies (config, OAuth client, etc.).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ticktick-server")
