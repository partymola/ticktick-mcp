"""Every tool result reaches the client as text alone.

Each tool returns JSON text. Left to infer it from the ``-> str`` annotation,
``mcp`` also declares an output schema and sends the same text again as
structured content, escaped inside ``{"result": ...}``. A client that hands the
model that copy instead of the text makes every reply longer, and the list
tools' character budget then no longer measures what the model reads.

conftest replaces the server instance and the auth decorator in this process,
so these tests start a fresh interpreter, register the tools the way
``ticktick-mcp`` does, and inspect the real server there.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import ticktick_mcp

_PROBE = textwrap.dedent(
    """
    import asyncio, json, sys

    from mcp.server.mcpserver import MCPServer

    MCPServer.run = lambda self, *args, **kwargs: None
    sys.argv = ["ticktick-mcp", "--dotenv-dir", sys.argv[1]]

    from ticktick_mcp import cli

    cli.main()

    from ticktick_mcp.mcp_instance import mcp


    async def probe():
        tools = await mcp.list_tools()
        result = await mcp.call_tool(
            "ticktick_convert_datetime_to_ticktick_format",
            {"datetime_iso_string": "2026-04-13T20:45:00+01:00", "tz": "Europe/London"},
        )
        return {
            "tools": {tool.name: tool.output_schema for tool in tools},
            "structured_content": result.structured_content,
            "text": [block.text for block in result.content],
        }


    print(json.dumps(asyncio.run(probe())))
    """
)


@pytest.fixture(scope="module")
def registered(tmp_path_factory):
    src = Path(ticktick_mcp.__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("TICKTICK")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(src), env.get("PYTHONPATH")]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path_factory.mktemp("dotenv"))],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def test_the_probe_sees_the_registered_tools(registered):
    assert "ticktick_convert_datetime_to_ticktick_format" in registered["tools"]
    assert "ticktick_update_task" in registered["tools"]


def test_no_tool_declares_an_output_schema(registered):
    assert registered["tools"], "no tools were registered"
    declaring = sorted(name for name, schema in registered["tools"].items() if schema is not None)
    assert declaring == []


def test_a_tool_call_returns_its_text_and_no_structured_content(registered):
    assert registered["structured_content"] is None
    assert registered["text"] == [json.dumps({"ticktick_format": "2026-04-13T19:45:00+0000"})]
