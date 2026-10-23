"""ASK_DIA_ENABLED kill switch: Ask DIA is OFF unless explicitly enabled."""
from pathlib import Path
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import agent_runtime
from app import ClientDiscoveryHandler, ask_dia_enabled, read_sample_questionnaire


def _request(method, path, body=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        headers = dict(resp.getheaders())
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return resp.status, (json.loads(raw) if raw else None), headers


@pytest.fixture
def no_model(monkeypatch):
    calls = []

    def fake_run(message):
        calls.append(message)
        return "pong"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    monkeypatch.setattr(agent_runtime, "is_configured", lambda: True)
    return calls


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off", "False", "maybe", "enabled"])
def test_off_unless_explicitly_enabled(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ASK_DIA_ENABLED", value)
    assert ask_dia_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " On "])
def test_explicit_values_enable(monkeypatch, value):
    monkeypatch.setenv("ASK_DIA_ENABLED", value)
    assert ask_dia_enabled() is True


def test_agent_off_by_default_returns_503_without_model_call(monkeypatch, no_model):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    status, payload, headers = _request("POST", "/api/agent", json.dumps({"message": "ping"}))
    assert status == 503
    assert payload == {"enabled": False, "reply": "Ask DIA is temporarily unavailable."}
    assert headers.get("Retry-After")
    assert no_model == []


def test_agent_off_rejects_even_malformed_body_without_model_call(monkeypatch, no_model):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    status, payload, _ = _request("POST", "/api/agent", "{bad json")
    assert status == 503
    assert payload["enabled"] is False
    assert no_model == []


def test_agent_explicit_true_enables(monkeypatch, no_model):
    monkeypatch.setenv("ASK_DIA_ENABLED", "true")
    status, payload, _ = _request("POST", "/api/agent", json.dumps({"message": "ping"}))
    assert status == 200
    assert payload == {"configured": True, "reply": "pong"}
    assert no_model == ["ping"]


def test_status_off_by_default(monkeypatch):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    status, payload, _ = _request("GET", "/api/agent/status")
    assert status == 200
    assert payload == {"configured": False, "enabled": False}


def test_status_when_enabled(monkeypatch, no_model):
    monkeypatch.setenv("ASK_DIA_ENABLED", "1")
    status, payload, _ = _request("GET", "/api/agent/status")
    assert status == 200
    assert payload == {"configured": True, "enabled": True}


def test_intake_still_works_when_ask_dia_off(monkeypatch, no_model):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    body = json.dumps({"questionnaire": read_sample_questionnaire()})
    status, payload, _ = _request("POST", "/api/process", body)
    assert status == 200
    assert payload["score"]["tier"]
    assert "client-profile.md" in payload["documents"]
    assert no_model == []


def test_config_unchanged_when_ask_dia_off(monkeypatch):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    status, payload, _ = _request("GET", "/api/config")
    assert status == 200


def test_ui_handles_disabled_state():
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "payload.enabled === false" in js
    assert "Ask DIA is temporarily unavailable." in js
    assert "function disableAskDia" in js
