"""Shared fixtures for the lockdown-v2 tests (sign-in gate, quota, counter).

All addresses are example.com; the signing key is a test-only constant.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import agent_auth
import agent_quota
import app
from agent_quota import DailyQuota, InMemoryQuotaStore

CLIENT_ID = "1234-test.apps.googleusercontent.com"
# Test-only signing key, not a real secret.
TEST_SESSION_KEY = "unit-test-session-signing-key-0123456789abcdef"
OWNERS = "Owner@Example.com, second.owner@example.com ,THIRD@example.com"

ENV_TO_CLEAR = (
    "GOOGLE_CLIENT_ID",
    "OWNER_EMAILS",
    "DIA_SESSION_SECRET",
    "DIA_SESSION_TTL_SECONDS",
    "K_SERVICE",
    "DIA_TRUST_FORWARDED_FOR",
    "DIA_BURST_LIMIT",
    "DIA_UTILITY_BURST_LIMIT",
    "DIA_AUTH_RATE_LIMIT",
    "DIA_GENERATION_DAILY_LIMIT",
    "DIA_ASK_DAILY_LIMIT",
    "DIA_QUOTA_STORE",
    "DIA_VISITOR_SALT",
    "ASK_DIA_ENABLED",
    "MEMORY_SHEET_ID",
    "OBS_CAPTURE",
)


def claims(**overrides):
    """A valid Google ID-token claim set (as google-auth would return it)."""
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 3600,
        "iat": time.time(),
        "sub": "sub-visitor",
        "email": "visitor@example.com",
        "email_verified": True,
    }
    base.update(overrides)
    return base


# Fake credential -> claims. The fake verifier stands in for google-auth's
# signature check: an unknown credential is a bad signature.
TOKENS = {
    "visitor-cred": claims(),
    "other-cred": claims(sub="sub-other", email="other@example.com"),
    "owner-cred": claims(sub="sub-owner", email="owner@example.com"),
    "owner2-cred": claims(sub="sub-owner2", email="SECOND.Owner@EXAMPLE.com"),
    "owner3-cred": claims(sub="sub-owner3", email="third@example.com"),
    "unverified-owner-cred": claims(sub="sub-unv", email="owner@example.com", email_verified=False),
    "expired-cred": claims(sub="sub-exp", exp=time.time() - 3600),
    "wrong-aud-cred": claims(sub="sub-aud", aud="someone-else.apps.googleusercontent.com"),
    "wrong-iss-cred": claims(sub="sub-iss", iss="https://evil.example.com"),
}


def fake_verify(token, audience):
    if token not in TOKENS:
        raise ValueError("Could not verify token signature.")
    data = dict(TOKENS[token])
    if token.startswith("expired"):
        data["exp"] = time.time() - 3600
    return data


@pytest.fixture
def gate_env(monkeypatch):
    """Sign-in configured, local-dev bypass OFF, fresh in-memory quota."""
    for name in ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DIA_LOCAL_DEV_NO_SIGN_IN", "false")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    monkeypatch.setenv("OWNER_EMAILS", OWNERS)
    monkeypatch.setenv("ASK_DIA_ENABLED", "true")
    monkeypatch.setenv("DIA_BURST_LIMIT", "off")
    monkeypatch.setenv("DIA_UTILITY_BURST_LIMIT", "off")
    monkeypatch.setenv("DIA_AUTH_RATE_LIMIT", "off")
    monkeypatch.setattr(agent_auth, "google_verifier", fake_verify)
    import agent_runtime

    calls = []

    def fake_run_agent(message):
        calls.append(message)
        return "fake agent reply"

    monkeypatch.setattr(agent_runtime, "is_configured", lambda: True)
    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    agent_auth.reset_limiters()
    agent_quota.set_quota(DailyQuota(InMemoryQuotaStore()))
    yield calls
    agent_quota.set_quota(None)
    agent_auth.reset_limiters()


class Server:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.ClientDiscoveryHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method, path, body=None, headers=None, cookie=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        if cookie:
            hdrs["Cookie"] = cookie
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload
        return resp.status, dict(resp.getheaders()), parsed

    def sign_in(self, credential="visitor-cred", headers=None):
        status, hdrs, payload = self.request(
            "POST", "/api/auth/session", {"credential": credential}, headers=headers
        )
        assert status == 200, payload
        cookie = hdrs["Set-Cookie"].split(";", 1)[0]
        return cookie, payload

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def server(gate_env):
    srv = Server()
    yield srv
    srv.close()
