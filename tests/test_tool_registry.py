"""Guards against a tool existing in only some of the three places it needs to.

A working tool needs a client method, a list_tools entry and a call_tool
branch. Miss one and the failure is quiet: the tool is either invisible to
every client or advertised and then unreachable. dry_run_pin shipped with a
call_tool branch and no list_tools entry, which is what these tests exist to
stop happening again.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from pinterest_mcp import server
from pinterest_mcp.client import PinterestClient

SERVER_SOURCE = Path(server.__file__).read_text(encoding="utf-8")


def _advertised_tools() -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def _dispatched_tools() -> set[str]:
    """Every tool name compared against in call_tool's if/elif chain."""
    tree = ast.parse(SERVER_SOURCE)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "name":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                names.add(comparator.value)
    return names


def test_every_advertised_tool_is_dispatched():
    missing = sorted(_advertised_tools() - _dispatched_tools())
    assert not missing, f"advertised in list_tools but unreachable in call_tool: {missing}"


def test_every_dispatched_tool_is_advertised():
    missing = sorted(_dispatched_tools() - _advertised_tools())
    assert not missing, f"handled in call_tool but invisible to clients: {missing}"


def test_dispatch_chain_was_actually_found():
    """Fail loudly if the AST walk stops matching, rather than passing vacuously."""
    assert len(_dispatched_tools()) > 20


@pytest.mark.parametrize("tool_name", sorted(_advertised_tools()))
def test_every_tool_has_a_client_method(tool_name: str):
    assert callable(getattr(PinterestClient, tool_name, None)), (
        f"{tool_name} is advertised but PinterestClient has no such method"
    )


@pytest.mark.parametrize("tool_name", sorted(_advertised_tools()))
def test_every_tool_description_states_its_trial_status(tool_name: str):
    """A future agent must not be misled by a call that 401s or is gated."""
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == tool_name)
    assert "TRIAL ACCESS" in (tool.description or "").upper(), (
        f"{tool_name} does not state its Trial-access status"
    )
