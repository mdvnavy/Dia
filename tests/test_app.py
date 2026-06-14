from pathlib import Path
from http.server import ThreadingHTTPServer
import http.client
import json
import threading

import pytest

from app import ClientDiscoveryHandler, build_intake_response, read_sample_questionnaire


def _request(method: str, path: str, body: str | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        status = response.status
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return status, json.loads(raw) if raw else None


def test_process_payload_returns_score_and_documents():
    fixture = Path("tests/fixtures/complete_questionnaire.md").read_text(encoding="utf-8")

    payload = build_intake_response(fixture)

    assert payload["score"]["tier"] == "Custom AI Agent"
    assert payload["score"]["total_score"] == 12
    assert "client-profile.md" in payload["documents"]
    assert "Northstar Studio" in payload["documents"]["client-profile.md"]


def test_process_payload_rejects_empty_questionnaire():
    with pytest.raises(ValueError, match="questionnaire is required"):
        build_intake_response("")


def test_process_endpoint_returns_invalid_json_for_malformed_body():
    status, payload = _request("POST", "/api/process", "{bad json")

    assert status == 400
    assert payload == {"error": "invalid json"}


def test_healthz_endpoint_reports_ok():
    status, payload = _request("GET", "/healthz")

    assert status == 200
    assert payload == {"status": "ok"}


def test_sample_endpoint_serves_runtime_questionnaire():
    status, payload = _request("GET", "/api/sample")

    assert status == 200
    assert "Northstar Studio" in payload["questionnaire"]


def test_read_sample_questionnaire_does_not_depend_on_tests_dir():
    # The runtime sample must live outside tests/ so it ships in the image.
    assert "Discovery Intake Questionnaire" in read_sample_questionnaire()


def test_agent_endpoint_requires_message():
    status, payload = _request("POST", "/api/agent", json.dumps({"message": ""}))

    assert status == 400
    assert payload == {"error": "message is required"}


def test_agent_endpoint_degrades_gracefully_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    status, payload = _request("POST", "/api/agent", json.dumps({"message": "hi"}))

    assert status == 200
    assert payload["configured"] is False
    assert "not configured" in payload["reply"].lower()
