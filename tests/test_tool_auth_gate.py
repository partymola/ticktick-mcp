"""Every client-using tool carries @require_ticktick_client.

This has to be a static check. conftest.py replaces the decorator with a no-op
before any tool module imports it, so at runtime the gate is invisible - a tool
that lost it would leave the whole suite green. Reading the source is the only
way the suite can witness it.
"""

import ast
import pathlib

import pytest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "ticktick_mcp" / "tools"

# Tools that genuinely never touch the client. Keep this list short and
# justified: an entry here is a tool that can run during an auth outage.
NO_CLIENT_NEEDED = {
    "ticktick_convert_datetime_to_ticktick_format",  # pure datetime formatting
}


def _decorators(node):
    names = set()
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        names.add(getattr(target, "id", None) or getattr(target, "attr", None))
    return names


def _mcp_tools():
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "tool" in _decorators(node):
                yield path.name, node


def test_the_scan_finds_the_tools_at_all():
    """A guard that silently matches nothing always passes."""
    found = list(_mcp_tools())
    assert len(found) >= 10, f"only found {len(found)} @mcp.tool functions - scan is broken"


@pytest.mark.parametrize(
    "filename,name,decorators",
    [(f, n.name, _decorators(n)) for f, n in _mcp_tools()],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_client_using_tools_are_gated(filename, name, decorators):
    if name in NO_CLIENT_NEEDED:
        assert "require_ticktick_client" not in decorators, (
            f"{name} is listed as client-free but carries the gate - update the list"
        )
        return
    assert "require_ticktick_client" in decorators, (
        f"{filename}:{name} reaches the client but has no @require_ticktick_client. "
        "Without it a client-less call proceeds on a None client - for the completion "
        "tools that writes a project NAME as the database key, which no later "
        "id-keyed read can find and no tool can repair."
    )
