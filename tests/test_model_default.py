"""Ask DIA's model id: current default (2.5 retired 2026-10-20) and the DIA_MODEL override."""

from pathlib import Path

import character


def _model_id(agent) -> str:
    # A plain string today; a Vertex-pinned Gemini wrapper once fix/enforce-vertex lands.
    return getattr(agent.model, "model", agent.model)

ROOT = Path(__file__).resolve().parents[1]


def test_default_is_the_current_replacement(monkeypatch):
    monkeypatch.delenv("DIA_MODEL", raising=False)
    assert character.DEFAULT_MODEL == "gemini-3.8-flash"
    assert character.agent_model() == "gemini-3.8-flash"
    assert _model_id(character.build_agent()) == "gemini-3.8-flash"


def test_dia_model_env_overrides(monkeypatch):
    monkeypatch.setenv("DIA_MODEL", "  pinned-model  ")
    assert character.agent_model() == "pinned-model"
    assert _model_id(character.build_agent()) == "pinned-model"
    monkeypatch.setenv("DIA_MODEL", "   ")
    assert character.agent_model() == "gemini-3.8-flash"


def test_no_retired_2_5_model_ids_in_app_code_or_diagrams():
    for rel in ["character.py", "agent_runtime.py", "app.py", "docs/architecture.mmd", "docs/architecture.svg"]:
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            if "gemini-2.5" in line:
                assert "retired" in line, (rel, line)
