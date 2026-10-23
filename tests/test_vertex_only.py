"""Vertex AI is the only way DIA reaches Gemini (house rule, as in Tia).

No test here touches the network: clients are constructed but never called.
"""

import os
import re
from pathlib import Path

import pytest

import agent_runtime
import character
import vertex_config
from vertex_config import VertexNotConfigured, vertex_gemini, vertex_settings

ROOT = Path(__file__).resolve().parents[1]
VERTEX_VARS = ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*VERTEX_VARS, "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(name, raising=False)


def _vertex(monkeypatch, project="test-project", location="us-central1"):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", project)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", location)


def test_vertex_settings_requires_flag_project_and_location(monkeypatch):
    with pytest.raises(VertexNotConfigured, match="GOOGLE_GENAI_USE_VERTEXAI"):
        vertex_settings()
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    with pytest.raises(VertexNotConfigured, match="GOOGLE_CLOUD_PROJECT"):
        vertex_settings()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    with pytest.raises(VertexNotConfigured):
        vertex_settings()
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    assert vertex_settings() == ("p", "us-central1")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    with pytest.raises(VertexNotConfigured):
        vertex_settings()


def test_api_key_alone_never_counts_as_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
    assert agent_runtime.is_configured() is False
    with pytest.raises(agent_runtime.AgentNotConfigured, match="Vertex AI"):
        agent_runtime.run_agent("hello")


def test_model_client_is_vertex_and_drops_env_api_key(monkeypatch):
    _vertex(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    client = vertex_gemini("some-model").api_client
    assert client.vertexai is True
    inner = client._api_client
    assert inner.api_key is None
    assert inner.project == "test-project" and inner.location == "us-central1"
    assert "x-goog-api-key" not in {k.lower() for k in (inner._http_options.headers or {})}


def test_model_client_refuses_when_vertex_is_off_even_with_a_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    with pytest.raises(VertexNotConfigured):
        _ = vertex_gemini("some-model").api_client


def test_agent_model_is_the_vertex_wrapper_and_dia_model_still_overrides(monkeypatch):
    _vertex(monkeypatch)
    monkeypatch.setenv("DIA_MODEL", "my-pinned-model")
    agent = character.build_agent()
    assert agent.model.model == "my-pinned-model"
    assert agent.model.api_client.vertexai is True
    assert agent.model.api_client._api_client.api_key is None
    # The module-level agent exists without env and is Vertex-only too.
    assert type(character.root_agent.model).__name__ == "VertexGemini"


def test_no_app_code_reads_an_api_key_or_builds_an_api_key_client():
    reads_key = re.compile(r"environ(?:\.get)?\s*[\(\[]\s*[\"'](?:GEMINI|GOOGLE)_API_KEY")
    for name in ("agent_runtime.py", "character.py", "app.py", "vertex_config.py",
                 "client_discovery/service.py", "client_discovery/config.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not reads_key.search(text), name
        assert "genai.Client(" not in text and "api_key=" not in text, name


def test_deploy_script_binds_no_api_key_and_sets_vertex_env():
    script = (ROOT / "deploy-cloudrun.sh").read_text(encoding="utf-8")
    assert "--set-secrets" not in script
    assert "GEMINI_API_KEY=" not in script
    assert "--remove-secrets GEMINI_API_KEY" in script
    assert "GOOGLE_GENAI_USE_VERTEXAI=TRUE" in script
    assert "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" in script
    assert "GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION}" in script
    # Keep the service's other env vars: never replace them wholesale.
    assert "--set-env-vars" not in script
