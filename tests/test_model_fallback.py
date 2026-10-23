"""Model selection, per-turn timeout and primary -> fallback behaviour.

Two layers of tests:
- fast unit tests that stub ``agent_runtime._run_turn``;
- end-to-end tests that run the real ADK Runner/LlmAgent with a *mocked
  google-genai client* (no network, no API key), so the fallback is proven
  through the same code path Cloud Run uses.
"""

import asyncio
import time

import pytest
from google.genai import types

import agent_runtime
import character


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("DIA_MODEL", "DIA_FALLBACK_MODEL", "DIA_AGENT_TIMEOUT_SECONDS", "MEMORY_SHEET_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DIA_AGENT_MCP_MODE", "none")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")


# --- configuration --------------------------------------------------------


def test_defaults_are_current_stable_models():
    assert character.primary_model() == "gemini-3.8-flash"
    assert character.fallback_model() == "gemini-3.5-flash-lite"
    assert character.build_agent().model == "gemini-3.8-flash"


def test_models_are_env_overridable(monkeypatch):
    monkeypatch.setenv("DIA_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("DIA_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    assert character.primary_model() == "gemini-3.6-flash"
    assert character.fallback_model() == "gemini-3.1-flash-lite"
    assert character.build_agent(model="gemini-3.1-flash-lite").model == "gemini-3.1-flash-lite"


@pytest.mark.parametrize("value", ["none", "OFF", "0", "false"])
def test_fallback_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("DIA_FALLBACK_MODEL", value)
    assert character.fallback_model() is None


def test_blank_env_values_mean_defaults(monkeypatch):
    # A copied .env.example leaves these blank.
    monkeypatch.setenv("DIA_MODEL", "")
    monkeypatch.setenv("DIA_FALLBACK_MODEL", "  ")
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", "")
    assert character.primary_model() == "gemini-3.8-flash"
    assert character.fallback_model() == "gemini-3.5-flash-lite"
    assert agent_runtime.turn_timeout_seconds() == 55.0


def test_fallback_equal_to_primary_is_ignored(monkeypatch):
    monkeypatch.setenv("DIA_MODEL", "gemini-3.5-flash-lite")
    assert character.fallback_model() is None


@pytest.mark.parametrize(("raw", "expected"), [("45", 45.0), ("60", 60.0), ("1", 5.0), ("9999", 300.0), ("abc", 55.0)])
def test_turn_timeout_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", raw)
    assert agent_runtime.turn_timeout_seconds() == expected


def test_default_turn_timeout_is_within_45_to_60s():
    assert 45 <= agent_runtime.DEFAULT_TURN_TIMEOUT_SECONDS <= 60


# --- fallback with a stubbed turn -----------------------------------------


def _stub_turns(monkeypatch, behaviour):
    calls = []

    async def fake_turn(message, model=None):
        calls.append(model)
        return await behaviour(model)

    monkeypatch.setattr(agent_runtime, "_run_turn", fake_turn)
    return calls


def test_primary_success_does_not_call_fallback(monkeypatch):
    async def ok(model):
        return f"reply from {model}"

    calls = _stub_turns(monkeypatch, ok)
    assert agent_runtime.run_agent("hi") == "reply from gemini-3.8-flash"
    assert calls == ["gemini-3.8-flash"]


def test_primary_error_falls_back(monkeypatch):
    async def flaky(model):
        if model == "gemini-3.8-flash":
            raise RuntimeError("503 UNAVAILABLE")
        return f"reply from {model}"

    calls = _stub_turns(monkeypatch, flaky)
    assert agent_runtime.run_agent("hi") == "reply from gemini-3.5-flash-lite"
    assert calls == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_primary_timeout_falls_back_within_budget(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", "5")  # primary gets 3.5s

    async def slow_primary(model):
        if model == "gemini-3.8-flash":
            await asyncio.sleep(30)
        return f"reply from {model}"

    calls = _stub_turns(monkeypatch, slow_primary)
    started = time.monotonic()
    assert agent_runtime.run_agent("hi") == "reply from gemini-3.5-flash-lite"
    assert time.monotonic() - started < 5
    assert calls == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_both_models_timing_out_raises_agent_timeout(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", "5")

    async def hang(model):
        await asyncio.sleep(30)

    _stub_turns(monkeypatch, hang)
    started = time.monotonic()
    with pytest.raises(agent_runtime.AgentTimeout):
        agent_runtime.run_agent("hi")
    assert time.monotonic() - started < 6.5  # one shared per-turn budget


def test_no_fallback_surfaces_primary_error(monkeypatch):
    monkeypatch.setenv("DIA_FALLBACK_MODEL", "none")

    async def broken(model):
        raise RuntimeError("400 model not found")

    calls = _stub_turns(monkeypatch, broken)
    with pytest.raises(RuntimeError, match="model not found"):
        agent_runtime.run_agent("hi")
    assert calls == ["gemini-3.8-flash"]


def test_no_fallback_timeout_uses_whole_budget(monkeypatch):
    monkeypatch.setenv("DIA_FALLBACK_MODEL", "none")
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", "5")

    async def hang(model):
        await asyncio.sleep(30)

    _stub_turns(monkeypatch, hang)
    with pytest.raises(agent_runtime.AgentTimeout, match="5s"):
        agent_runtime.run_agent("hi")


def test_endpoint_maps_timeout_to_504(monkeypatch):
    from tests.test_app import _request
    import json

    def timeout(_message):
        raise agent_runtime.AgentTimeout("neither model replied within 55s")

    monkeypatch.setattr(agent_runtime, "run_agent", timeout)
    status, payload = _request("POST", "/api/agent", json.dumps({"message": "hi"}))
    assert status == 504
    assert "timed out" in payload["error"]


# --- end to end through ADK with a mocked genai client --------------------


class _FakeModels:
    def __init__(self, owner_model, script):
        self._model = owner_model
        self._script = script

    async def generate_content(self, *, model, contents, config=None, **_):
        self._script["seen"].append(model)
        action = self._script[model]
        if isinstance(action, Exception):
            raise action
        if action == "hang":
            await asyncio.sleep(30)
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(role="model", parts=[types.Part(text=action)]),
                    finish_reason=types.FinishReason.STOP,
                )
            ]
        )

    async def generate_content_stream(self, **kwargs):  # pragma: no cover - not used
        raise AssertionError("streaming not expected")


class _FakeClient:
    def __init__(self, owner_model, script):
        self.aio = type("Aio", (), {})()
        self.aio.models = _FakeModels(owner_model, script)
        self.vertexai = False


@pytest.fixture
def fake_genai(monkeypatch):
    from google.adk.models.google_llm import Gemini

    script = {"seen": []}
    monkeypatch.setattr(
        Gemini, "api_client", property(lambda self: _FakeClient(self.model, script))
    )
    return script


def test_adk_turn_uses_primary_model(fake_genai):
    fake_genai["gemini-3.8-flash"] = "Primary says hi."
    assert agent_runtime.run_agent("hello") == "Primary says hi."
    assert fake_genai["seen"] == ["gemini-3.8-flash"]


def test_adk_turn_falls_back_on_client_error(fake_genai):
    from google.genai import errors

    fake_genai["gemini-3.8-flash"] = errors.ClientError(
        404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}}
    )
    fake_genai["gemini-3.5-flash-lite"] = "Fallback says hi."
    assert agent_runtime.run_agent("hello") == "Fallback says hi."
    assert fake_genai["seen"][0] == "gemini-3.8-flash"
    assert fake_genai["seen"][-1] == "gemini-3.5-flash-lite"


def test_adk_turn_falls_back_on_timeout(monkeypatch, fake_genai):
    monkeypatch.setenv("DIA_AGENT_TIMEOUT_SECONDS", "5")
    fake_genai["gemini-3.8-flash"] = "hang"
    fake_genai["gemini-3.5-flash-lite"] = "Fallback after timeout."
    started = time.monotonic()
    assert agent_runtime.run_agent("hello") == "Fallback after timeout."
    assert time.monotonic() - started < 5
