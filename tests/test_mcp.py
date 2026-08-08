"""MCP shim tests.

The shim must stay logic-free, so what matters is: the right tools exist, they
are described well enough for an agent to choose correctly, and a dead backend
produces an actionable message rather than a stack trace.
"""

import asyncio
import json

import pytest

import mcp_server

EXPECTED_TOOLS = {
    "search_eazli_kb",
    "search_design_principles",
    "search_products",
    "list_home_units",
    "get_room",
    "check_fit",
    "validate_layout",
    "check_access_path",
    "check_plan_quota",
}


@pytest.fixture(scope="module")
def tools():
    return asyncio.run(mcp_server.mcp.list_tools())


def test_every_expected_tool_is_registered(tools):
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_has_a_description_an_agent_can_act_on(tools):
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 80, f"{tool.name} description is too thin to choose on"


def test_access_path_tool_tells_the_agent_to_prefer_carton_dimensions(tools):
    desc = next(t for t in tools if t.name == "check_access_path").description
    assert "carton" in desc.lower()


def test_kb_tool_warns_that_analysis_is_not_eazli_published(tools):
    desc = next(t for t in tools if t.name == "search_eazli_kb").description
    assert "analysis" in desc.lower()
    assert "not published by eazli" in desc.lower() or "NOT published" in desc


def test_unreachable_backend_returns_actionable_json(monkeypatch):
    monkeypatch.setattr(mcp_server, "API", "http://127.0.0.1:9")
    body = json.loads(mcp_server._call("GET", "/health"))
    assert "error" in body
    assert "uvicorn" in body["fix"]


def test_check_fit_tool_exposes_role(tools):
    """Regression: making an unknown role 'unverified' left both the MCP tool
    and the CLI with no way to declare one, so every fit check came back
    unverified — the fix broke the callers it was meant to protect."""
    schema = next(t for t in tools if t.name == "check_fit").input_schema
    assert "role" in schema["properties"]
    assert "role" in next(t for t in tools if t.name == "check_fit").description
