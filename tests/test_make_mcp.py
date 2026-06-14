"""Tests for the optional Make MCP Toolbox wiring in character.py."""

import importlib
import os
from unittest.mock import patch

import character


def _reload_with_env(env: dict):
    with patch.dict(os.environ, env, clear=False):
        return importlib.reload(character)


def test_toolset_disabled_without_url(monkeypatch):
    monkeypatch.delenv("MAKE_MCP_URL", raising=False)
    assert character._make_mcp_toolset() == []


def test_toolset_enabled_with_url(monkeypatch):
    monkeypatch.setenv("MAKE_MCP_URL", "https://us2.make.com/mcp/api/v1/u/test-token/mcp")
    monkeypatch.setenv("MAKE_MCP_TOKEN", "test-bearer")
    toolsets = character._make_mcp_toolset()
    assert len(toolsets) == 1
    params = toolsets[0]._connection_params
    assert params.url.startswith("https://us2.make.com/")
    assert params.headers["Authorization"] == "Bearer test-bearer"
    assert "text/event-stream" in params.headers["Accept"]


def test_toolset_does_not_log_make_url(monkeypatch, caplog):
    url = "https://us2.make.com/mcp/api/v1/u/test-token/mcp"
    monkeypatch.setenv("MAKE_MCP_URL", url)
    monkeypatch.setenv("MAKE_MCP_TOKEN", "test-bearer")
    caplog.set_level("INFO", logger="character")

    character._make_mcp_toolset()

    assert url not in caplog.text
    assert "test-token" not in caplog.text


def test_toolset_omits_auth_header_without_token(monkeypatch):
    monkeypatch.setenv("MAKE_MCP_URL", "https://us2.make.com/mcp/api/v1/u/test-token/mcp")
    monkeypatch.delenv("MAKE_MCP_TOKEN", raising=False)
    toolsets = character._make_mcp_toolset()
    assert len(toolsets) == 1
    assert "Authorization" not in toolsets[0]._connection_params.headers


def test_root_agent_keeps_core_tools():
    # The deterministic pipeline tools must always be present regardless of
    # whether the Make toolbox is configured.
    tool_names = [getattr(t, "__name__", type(t).__name__) for t in character.root_agent.tools]
    for expected in (
        "parse_intake",
        "validate_intake_fields",
        "score_client_opportunity",
        "generate_intake_documents",
    ):
        assert expected in tool_names
