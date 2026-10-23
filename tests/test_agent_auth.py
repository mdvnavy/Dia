"""Access control and rate limiting for /api/agent (agent_auth.py)."""

from http.server import ThreadingHTTPServer
import http.client
import json
import threading

import pytest

import agent_auth
from agent_auth import RateLimiter, authorize, client_ip, configured_mode
from app import ClientDiscoveryHandler

TOKEN = "unit-test-token-not-a-secret"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "DIA_AGENT_TOKEN",
        "DIA_IAP_AUDIENCE",
        "K_SERVICE",
        "DIA_TRUST_FORWARDED_FOR",
        "DIA_AGENT_RATE_LIMIT",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    agent_auth.agent_rate_limiter.reset()
    yield
    agent_auth.agent_rate_limiter.reset()


# --- authorize() ----------------------------------------------------------


def test_local_dev_allows_loopback_only():
    assert configured_mode() == "local-only"
    assert authorize({}, "127.0.0.1").allowed
    assert authorize({}, "::1").allowed
    assert not authorize({}, "203.0.113.7").allowed
    assert not authorize({}, "not-an-ip").allowed


def test_cloud_run_without_auth_fails_closed(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "dia-discovery-intake")
    decision = authorize({}, "127.0.0.1")
    assert not decision.allowed
    assert "DIA_AGENT_TOKEN" in decision.reason


def test_token_mode_requires_matching_bearer(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TOKEN", TOKEN)
    assert configured_mode() == "token"
    assert authorize({"Authorization": f"Bearer {TOKEN}"}, "203.0.113.7").allowed
    assert authorize({"X-DIA-Agent-Token": TOKEN}, "203.0.113.7").allowed
    assert not authorize({"Authorization": "Bearer wrong"}, "203.0.113.7").allowed
    assert not authorize({"Authorization": TOKEN}, "203.0.113.7").allowed  # no scheme


def test_token_mode_fails_closed_even_for_loopback(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TOKEN", TOKEN)
    assert not authorize({}, "127.0.0.1").allowed


def test_spoofed_iap_identity_headers_are_not_trusted(monkeypatch):
    monkeypatch.setenv("DIA_IAP_AUDIENCE", "/projects/1/global/backendServices/2")
    headers = {
        "X-Goog-Authenticated-User-Email": "accounts.google.com:owner@example.com",
        "X-Goog-Authenticated-User-Id": "accounts.google.com:123",
    }
    assert not authorize(headers, "203.0.113.7").allowed


def test_iap_jwt_is_verified_against_audience(monkeypatch):
    audience = "/projects/1/global/backendServices/2"
    monkeypatch.setenv("DIA_IAP_AUDIENCE", audience)
    seen = {}

    def fake_verifier(assertion, aud):
        seen["args"] = (assertion, aud)
        return "owner@example.com" if assertion == "good.jwt" else None

    good = authorize(
        {"X-Goog-IAP-JWT-Assertion": "good.jwt"}, "203.0.113.7", iap_verifier=fake_verifier
    )
    assert good.allowed and good.principal == "owner@example.com"
    assert seen["args"] == ("good.jwt", audience)
    assert not authorize(
        {"X-Goog-IAP-JWT-Assertion": "forged.jwt"}, "203.0.113.7", iap_verifier=fake_verifier
    ).allowed


def test_real_iap_verifier_rejects_garbage_without_raising(monkeypatch):
    from google.oauth2 import id_token

    # No network: serve an empty key set instead of fetching IAP's certs.
    monkeypatch.setattr(id_token, "_fetch_certs", lambda request, url: {})
    assert agent_auth._verify_iap_jwt("not-a-jwt", "aud") is None


# --- client_ip() ----------------------------------------------------------


def test_forwarded_for_ignored_off_cloud_run():
    assert client_ip({"X-Forwarded-For": "1.2.3.4"}, "127.0.0.1") == "127.0.0.1"


def test_forwarded_for_uses_rightmost_entry_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "svc")
    headers = {"X-Forwarded-For": "6.6.6.6, 198.51.100.9"}  # client-forged, real
    assert client_ip(headers, "169.254.1.1") == "198.51.100.9"
    assert client_ip({}, "169.254.1.1") == "169.254.1.1"


# --- RateLimiter ----------------------------------------------------------


def test_rate_limiter_sliding_window(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "2/10")
    now = [100.0]
    limiter = RateLimiter(clock=lambda: now[0])

    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (True, 0)
    allowed, retry_after = limiter.check("a")
    assert not allowed and 1 <= retry_after <= 11
    assert limiter.check("b") == (True, 0)  # per-key

    now[0] += 10.0
    assert limiter.check("a") == (True, 0)


def test_rate_limiter_can_be_disabled_and_survives_bad_config(monkeypatch):
    limiter = RateLimiter(clock=lambda: 0.0)
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "0")
    assert all(limiter.check("a")[0] for _ in range(50))

    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "garbage")  # falls back to 10/60
    limiter.reset()
    results = [limiter.check("a")[0] for _ in range(11)]
    assert results.count(True) == 10 and results[-1] is False


# --- /api/agent end-to-end ------------------------------------------------


def _post_agent(headers=None, body='{"message": "hi"}'):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request(
            "POST",
            "/api/agent",
            body=body,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        result = resp.status, json.loads(raw), dict(resp.getheaders())
        conn.close()
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_endpoint_rejects_missing_token_before_touching_gemini(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    import agent_runtime

    def boom(_message):  # pragma: no cover - must never be reached
        raise AssertionError("run_agent called without auth")

    monkeypatch.setattr(agent_runtime, "run_agent", boom)

    status, payload, headers = _post_agent()
    assert status == 401
    assert "token" in payload["error"]
    assert headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_endpoint_accepts_valid_token(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    import agent_runtime

    monkeypatch.setattr(agent_runtime, "run_agent", lambda message: f"echo: {message}")

    status, payload, _ = _post_agent({"Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    assert payload == {"configured": True, "reply": "echo: hi"}


def test_endpoint_refuses_all_calls_on_cloud_run_without_auth(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "dia-discovery-intake")
    status, payload, _ = _post_agent()
    assert status == 401
    assert "disabled" in payload["error"]


def test_endpoint_rate_limits_per_ip(monkeypatch):
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "2/60")
    statuses = [_post_agent()[0] for _ in range(3)]
    assert statuses[:2] == [200, 200]  # loopback dev, no key -> graceful 200
    assert statuses[2] == 429
    _, payload, headers = _post_agent()
    assert "rate limit" in payload["error"]
    assert int(headers["Retry-After"]) >= 1


def test_status_endpoint_reports_auth_mode(monkeypatch):
    from tests.test_app import _request

    monkeypatch.setenv("DIA_AGENT_TOKEN", TOKEN)
    status, payload = _request("GET", "/api/agent/status")
    assert status == 200
    assert payload["auth"] == "token"
    assert TOKEN not in json.dumps(payload)
