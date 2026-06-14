import agent_runtime


def test_client_report_prompt_uses_core_tools_by_default(monkeypatch):
    monkeypatch.delenv("DIA_AGENT_MCP_MODE", raising=False)
    monkeypatch.delenv("DIA_AGENT_ALLOW_GCP_MCP_TOOLS", raising=False)

    assert agent_runtime._select_mcp_toolsets(
        "Recommend the best report tab to demo for this client."
    ) == (False, False)


def test_make_handoff_prompt_selects_make_tools(monkeypatch):
    monkeypatch.delenv("DIA_AGENT_MCP_MODE", raising=False)

    assert agent_runtime._select_mcp_toolsets(
        "Record lead in sheet and save proposal to Drive."
    ) == (True, False)


def test_gcp_prompt_requires_explicit_allow(monkeypatch):
    monkeypatch.delenv("DIA_AGENT_MCP_MODE", raising=False)
    monkeypatch.delenv("DIA_AGENT_ALLOW_GCP_MCP_TOOLS", raising=False)

    assert agent_runtime._select_mcp_toolsets(
        "Check Cloud Run monitoring dashboards."
    ) == (False, False)

    monkeypatch.setenv("DIA_AGENT_ALLOW_GCP_MCP_TOOLS", "true")
    assert agent_runtime._select_mcp_toolsets(
        "Check Cloud Run monitoring dashboards."
    ) == (False, True)


def test_mcp_mode_overrides_auto_selection(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_MCP_MODE", "all")
    assert agent_runtime._select_mcp_toolsets("plain client report") == (True, True)

    monkeypatch.setenv("DIA_AGENT_MCP_MODE", "none")
    assert agent_runtime._select_mcp_toolsets("record lead in sheet") == (False, False)
