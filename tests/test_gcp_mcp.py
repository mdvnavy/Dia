"""Tests for the optional GCP-managed MCP wiring in character.py."""

from unittest.mock import MagicMock, patch

import character


def test_toolset_disabled_without_url(monkeypatch):
    monkeypatch.delenv("GCP_MCP_URL", raising=False)
    assert character._gcp_mcp_toolset() == []


def test_toolset_disabled_when_adc_unavailable(monkeypatch):
    monkeypatch.setenv("GCP_MCP_URL", "https://monitoring.googleapis.com/mcp")
    with patch("google.auth.default", side_effect=Exception("no ADC here")):
        assert character._gcp_mcp_toolset() == []


def test_toolset_enabled_with_url_and_adc(monkeypatch):
    monkeypatch.setenv("GCP_MCP_URL", "https://monitoring.googleapis.com/mcp")
    credentials = MagicMock()
    credentials.token = "test-adc-token"
    with patch("google.auth.default", return_value=(credentials, "test-project")):
        toolsets = character._gcp_mcp_toolset()
    assert len(toolsets) == 1
    params = toolsets[0]._connection_params
    assert params.url == "https://monitoring.googleapis.com/mcp"
    assert params.headers["Authorization"] == "Bearer test-adc-token"
    assert "text/event-stream" in params.headers["Accept"]
    credentials.refresh.assert_called_once()
