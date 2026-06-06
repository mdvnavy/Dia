from pathlib import Path
from http.server import ThreadingHTTPServer
import http.client
import json
import threading

import pytest

from app import ClientDiscoveryHandler, build_intake_response


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
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/process",
            body="{bad json",
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status == 400
    assert payload == {"error": "invalid json"}
