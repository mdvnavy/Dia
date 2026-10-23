"""Daily visitor quota for /api/agent (agent_quota.py + app.py wiring)."""

import datetime as dt
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import agent_auth
import agent_quota
import app
from agent_quota import (
    DailyQuota,
    FirestoreQuotaStore,
    InMemoryQuotaStore,
    limit_message,
    normalise_ip,
    quota_day,
    visitor_keys,
)
from app import ClientDiscoveryHandler

CLIENT_ID = "1234-test.apps.googleusercontent.com"
# Test-only signing key, not a real secret.
TEST_SESSION_KEY = "unit-test-session-signing-key-0123456789abcdef"
UTC = dt.timezone.utc
ET = agent_quota.quota_tz()


class Clock:
    def __init__(self, when: dt.datetime) -> None:
        self.when = when

    def __call__(self) -> dt.datetime:
        return self.when


class FakeStore:
    """In-memory fake of the QuotaStore interface that records calls."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = {}
        self.calls: list[tuple] = []
        self.fail = False

    def incr(self, keys, day, expire_at):
        self.calls.append(("incr", tuple(keys), day, expire_at))
        if self.fail:
            raise ConnectionError("store down")
        out = []
        for key in keys:
            self.counts[(day, key)] = self.counts.get((day, key), 0) + 1
            out.append(self.counts[(day, key)])
        return out

    def get(self, keys, day):
        self.calls.append(("get", tuple(keys), day))
        if self.fail:
            raise ConnectionError("store down")
        return [self.counts.get((day, key), 0) for key in keys]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "GOOGLE_CLIENT_ID",
        "OWNER_EMAILS",
        "DIA_SESSION_SECRET",
        "K_SERVICE",
        "DIA_TRUST_FORWARDED_FOR",
        "DIA_AGENT_RATE_LIMIT",
        "DIA_AGENT_DAILY_LIMIT",
        "DIA_QUOTA_STORE",
        "DIA_VISITOR_SALT",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "0")  # isolate the daily cap
    agent_auth.agent_rate_limiter.reset()
    app.status_rate_limiter.reset()
    app.auth_rate_limiter.reset()
    agent_quota.set_quota(None)
    yield
    agent_quota.set_quota(None)
    agent_auth.agent_rate_limiter.reset()
    app.status_rate_limiter.reset()


# --- day boundary ---------------------------------------------------------


def test_quota_day_is_new_york_calendar_day():
    # 03:30 UTC on Oct 23 is still Oct 22 in New York (UTC-4 in October).
    day, reset = quota_day(dt.datetime(2026, 10, 23, 3, 30, tzinfo=UTC))
    assert day == "2026-10-22"
    assert reset == dt.datetime(2026, 10, 23, tzinfo=ET)
    assert reset.utcoffset() == dt.timedelta(hours=-4)
    # Winter: UTC-5.
    day, reset = quota_day(dt.datetime(2026, 12, 1, 4, 59, tzinfo=UTC))
    assert day == "2026-11-30"
    assert reset.astimezone(UTC) == dt.datetime(2026, 12, 1, 5, 0, tzinfo=UTC)


# --- policy with the store interface -------------------------------------


def test_tenth_ok_eleventh_denied_with_fake_store():
    store = FakeStore()
    quota = DailyQuota(store, clock=Clock(dt.datetime(2026, 10, 23, 15, tzinfo=UTC)))
    keys = visitor_keys("198.51.100.9", "visitor-cookie-abcdef123456")
    results = [quota.consume(keys) for _ in range(11)]
    assert all(r.allowed for r in results[:10])
    assert results[9].remaining == 0
    assert not results[10].allowed
    assert results[10].remaining == 0
    # Both buckets were charged in one store call per request.
    assert all(len(call[1]) == 2 for call in store.calls if call[0] == "incr")
    # Once over, the instance remembers and stops hitting the store today.
    calls_before = len(store.calls)
    assert not quota.consume(keys).allowed
    assert len(store.calls) == calls_before


def test_resets_at_new_york_midnight_with_mocked_clock():
    clock = Clock(dt.datetime(2026, 10, 23, 23, 59, 30, tzinfo=ET))
    quota = DailyQuota(FakeStore(), clock=clock)
    keys = visitor_keys("198.51.100.9", None)
    for _ in range(10):
        assert quota.consume(keys).allowed
    denied = quota.consume(keys)
    assert not denied.allowed
    assert denied.retry_after(clock()) == 31  # seconds until midnight ET (+1)

    clock.when = dt.datetime(2026, 10, 24, 0, 0, 1, tzinfo=ET)
    fresh = quota.consume(keys)
    assert fresh.allowed and fresh.used == 1


def test_clearing_cookie_does_not_reset_cap():
    quota = DailyQuota(FakeStore(), clock=Clock(dt.datetime(2026, 10, 23, 15, tzinfo=UTC)))
    for _ in range(10):
        assert quota.consume(visitor_keys("198.51.100.9", "cookie-one-abcdefghijkl")).allowed
    # Same IP, new cookie (cookies cleared): still capped by the IP bucket.
    assert not quota.consume(visitor_keys("198.51.100.9", "cookie-two-abcdefghijkl")).allowed
    # Same cookie from a new network: still capped by the cookie bucket.
    assert not quota.consume(visitor_keys("203.0.113.50", "cookie-one-abcdefghijkl")).allowed
    # A genuinely different visitor is unaffected.
    assert quota.consume(visitor_keys("203.0.113.77", "cookie-three-abcdefghij")).allowed


def test_store_failure_falls_back_to_in_memory_cap():
    store = FakeStore()
    store.fail = True
    quota = DailyQuota(store, clock=Clock(dt.datetime(2026, 10, 23, 15, tzinfo=UTC)))
    keys = visitor_keys("198.51.100.9", None)
    results = [quota.consume(keys).allowed for _ in range(11)]
    assert results == [True] * 10 + [False]  # still capped, never unlimited
    assert quota.peek(keys).used == 11


def test_in_memory_store_counts_per_day_and_drops_old_days():
    store = InMemoryQuotaStore()
    expire = dt.datetime(2026, 10, 25, tzinfo=UTC)
    assert store.incr(["a", "b"], "2026-10-23", expire) == [1, 1]
    assert store.incr(["a"], "2026-10-23", expire) == [2]
    assert store.get(["a", "b", "c"], "2026-10-23") == [2, 1, 0]
    assert store.incr(["a"], "2026-10-24", expire) == [1]
    assert store.get(["a"], "2026-10-23") == [0]  # previous day pruned


def test_keys_are_hashed_and_ipv6_collapses_to_64():
    keys = visitor_keys("198.51.100.9", "visitor-cookie-abcdef123456")
    assert keys[0].startswith("ip:") and keys[1].startswith("vid:")
    assert "198.51.100.9" not in "".join(keys)
    assert normalise_ip("2001:db8:1:2:aaaa::1") == normalise_ip("2001:db8:1:2:bbbb::9")
    assert normalise_ip("::ffff:198.51.100.9") == "198.51.100.9"
    # Malformed cookies are ignored rather than used as keys.
    assert len(visitor_keys("198.51.100.9", "bad cookie;")) == 1


def test_limit_message_is_friendly():
    assert limit_message(10) == "You've used today's 10 questions. Come back tomorrow!"


# --- Firestore REST store with a fake session ----------------------------


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.counts = {}
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        if url.endswith(":commit"):
            results = []
            for write in json["writes"]:
                name = write["update"]["name"]
                self.counts[name] = self.counts.get(name, 0) + 1
                results.append({"transformResults": [{"integerValue": str(self.counts[name])}]})
            return FakeResponse({"writeResults": results})
        if url.endswith(":batchGet"):
            out = []
            for name in json["documents"]:
                if name in self.counts:
                    out.append({"found": {"name": name, "fields": {"count": {"integerValue": str(self.counts[name])}}}})
                else:
                    out.append({"missing": name})
            return FakeResponse(out)
        raise AssertionError(url)


def test_firestore_store_uses_atomic_increment_commit():
    session = FakeSession()
    store = FirestoreQuotaStore(project="demo-proj", session_factory=lambda: session)
    expire = dt.datetime(2026, 10, 25, 4, tzinfo=UTC)
    assert store.incr(["ip:abc", "vid:def"], "2026-10-23", expire) == [1, 1]
    assert store.incr(["ip:abc"], "2026-10-23", expire) == [2]
    url, body, timeout = session.posts[0]
    assert url == (
        "https://firestore.googleapis.com/v1/projects/demo-proj/databases/(default)/documents:commit"
    )
    write = body["writes"][0]
    assert write["update"]["name"].endswith("/documents/dia_agent_quota/2026-10-23_ip-abc")
    assert write["update"]["fields"]["expire_at"] == {"timestampValue": "2026-10-25T04:00:00Z"}
    assert write["updateTransforms"] == [{"fieldPath": "count", "increment": {"integerValue": "1"}}]
    assert timeout and timeout <= 5
    assert store.get(["ip:abc", "vid:def", "vid:none"], "2026-10-23") == [2, 1, 0]


def test_store_from_env(monkeypatch):
    assert agent_quota.store_from_env() == (None, "memory")
    monkeypatch.setenv("DIA_QUOTA_STORE", "firestore")
    store, name = agent_quota.store_from_env()
    assert name == "firestore" and isinstance(store, FirestoreQuotaStore)


# --- /api/agent end to end -----------------------------------------------


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _call(server, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    headers_out = {k.lower(): v for k, v in resp.getheaders()}
    # Keep every Set-Cookie (session + visitor can both be set).
    headers_out["set-cookie-all"] = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
    if not headers_out["set-cookie-all"]:
        del headers_out["set-cookie-all"]
    result = resp.status, json.loads(raw), headers_out
    conn.close()
    return result


@pytest.fixture
def live_agent(monkeypatch):
    """Server with a fake 'configured' agent that never calls Gemini."""
    import agent_runtime

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    calls = []

    def fake_run(message):
        calls.append(message)
        return f"echo: {message}"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run)
    agent_quota.set_quota(
        DailyQuota(FakeStore(), clock=Clock(dt.datetime(2026, 10, 23, 15, tzinfo=UTC)))
    )
    server, thread = _serve()
    yield server, calls
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


ASK = json.dumps({"message": "hi"})


def test_endpoint_tenth_ok_eleventh_429_with_friendly_message(live_agent):
    server, calls = live_agent
    cookie = {"Cookie": "dia_vid=visitor-cookie-abcdef123456"}
    for i in range(10):
        status, payload, _ = _call(server, "POST", "/api/agent", cookie, ASK)
        assert status == 200, payload
        assert payload["quota"]["remaining"] == 9 - i
    status, payload, headers = _call(server, "POST", "/api/agent", cookie, ASK)
    assert status == 429
    assert payload["error"] == "You've used today's 10 questions. Come back tomorrow!"
    assert payload["quota"]["remaining"] == 0
    assert int(headers["retry-after"]) > 0
    assert len(calls) == 10  # the 11th never reached the agent


def test_endpoint_mints_visitor_cookie(live_agent):
    server, _ = live_agent
    status, _, headers = _call(server, "POST", "/api/agent", {}, ASK)
    assert status == 200
    cookie = headers["set-cookie"]
    assert cookie.startswith("dia_vid=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie  # plain-http local dev
    # A request that already carries a valid cookie doesn't get a new one.
    value = cookie.split(";")[0]
    _, _, headers = _call(server, "POST", "/api/agent", {"Cookie": value}, ASK)
    assert "set-cookie" not in headers


def test_spoofed_xff_does_not_bypass_cap_on_cloud_run(live_agent, monkeypatch):
    server, calls = live_agent
    monkeypatch.setenv("K_SERVICE", "dia-discovery-intake")
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "3")
    real = "198.51.100.9"  # what Google's front end appends (right-most)
    statuses = []
    for i in range(6):
        headers = {"X-Forwarded-For": f"10.9.8.{i}, {real}"}  # rotating forged left hop, no cookie
        statuses.append(_call(server, "POST", "/api/agent", headers, ASK)[0])
    assert statuses == [200, 200, 200, 429, 429, 429]
    assert len(calls) == 3


def test_unconfigured_agent_and_bad_requests_are_not_charged(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    server, thread = _serve()
    try:
        for _ in range(3):
            status, payload, _ = _call(server, "POST", "/api/agent", {}, ASK)
            assert status == 200 and payload["configured"] is False
        assert _call(server, "POST", "/api/agent", {}, json.dumps({"message": ""}))[0] == 400
        assert agent_quota.get_quota().peek(visitor_keys("127.0.0.1", None)).used == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_burst_guard_still_applies_to_visitors(live_agent, monkeypatch):
    server, _ = live_agent
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "2/60")
    statuses = [_call(server, "POST", "/api/agent", {}, ASK)[0] for _ in range(3)]
    assert statuses == [200, 200, 429]
    _, payload, _ = _call(server, "POST", "/api/agent", {}, ASK)
    assert "Too many questions at once" in payload["error"]


# --- Google sign-in end to end ------------------------------------------------


def _claims(email, **overrides):
    import time

    base = {
        "iss": "accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 3600,
        "sub": f"sub-{email}",
        "email": email,
        "email_verified": True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    monkeypatch.setenv("OWNER_EMAILS", "Owner@Example.com")
    tokens = {
        "owner-cred": _claims("owner@example.com"),
        "visitor-cred": _claims("visitor@example.com"),
        "bad-aud": _claims("owner@example.com", aud="other.apps.googleusercontent.com"),
        "bad-iss": _claims("owner@example.com", iss="https://evil.example.com"),
        "tok-old": _claims("owner@example.com", exp=1_000),
        "unverified": _claims("owner@example.com", email_verified=False),
    }

    def fake_verify(token, audience):
        if token not in tokens:
            raise ValueError("bad signature")
        return tokens[token]

    monkeypatch.setattr(agent_auth, "google_verifier", fake_verify)
    return tokens


def _sign_in(server, credential):
    status, payload, headers = _call(
        server, "POST", "/api/auth/session", {}, json.dumps({"credential": credential})
    )
    cookies = [c for c in headers.get("set-cookie-all", []) if c.startswith("dia_session=")]
    session = cookies[0].split(";")[0] if cookies else None
    return status, payload, session, cookies


def test_auth_config_hides_sign_in_without_client_id(live_agent, monkeypatch):
    server, _ = live_agent
    _, payload, _ = _call(server, "GET", "/api/auth/config")
    assert payload["enabled"] is False and payload["google_client_id"] is None
    status, _, _ = _call(server, "POST", "/api/auth/session", {}, json.dumps({"credential": "x"}))
    assert status == 404
    # The cap still applies with sign-in off.
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    assert _call(server, "POST", "/api/agent", {}, ASK)[0] == 200
    assert _call(server, "POST", "/api/agent", {}, ASK)[0] == 429


def test_auth_config_exposes_client_id_when_configured(live_agent, google):
    server, _ = live_agent
    _, payload, _ = _call(server, "GET", "/api/auth/config")
    assert payload == {
        "enabled": True,
        "google_client_id": CLIENT_ID,
        "signed_in": False,
        "email": None,
        "owner": False,
    }
    assert TEST_SESSION_KEY not in json.dumps(payload)


def test_valid_owner_is_unlimited_and_skips_burst_guard(live_agent, google, monkeypatch):
    server, calls = live_agent
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "2/60")
    status, payload, session, cookies = _sign_in(server, "owner-cred")
    assert status == 200 and payload == {"signed_in": True, "email": "owner@example.com", "owner": True}
    cookie = cookies[0]
    for part in ("HttpOnly", "SameSite=Lax", "Max-Age=43200", "Path=/"):
        assert part in cookie
    assert "owner-cred" not in cookie
    for _ in range(6):  # over both the daily cap (1) and the burst guard (2/60)
        status, payload, _ = _call(server, "POST", "/api/agent", {"Cookie": session}, ASK)
        assert status == 200 and payload["owner"] is True and "quota" not in payload
    assert len(calls) == 6
    _, payload, _ = _call(server, "GET", "/api/agent/status", {"Cookie": session})
    assert payload["owner"] is True and payload["quota"] is None and payload["auth"] == "google"


def test_valid_non_owner_is_capped_like_a_visitor(live_agent, google, monkeypatch):
    server, calls = live_agent
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "2")
    status, payload, session, _ = _sign_in(server, "visitor-cred")
    assert status == 200 and payload["owner"] is False and payload["signed_in"] is True
    jar = {"Cookie": f"{session}; dia_vid=visitor-cookie-abcdef123456"}
    statuses = [_call(server, "POST", "/api/agent", jar, ASK)[0] for _ in range(3)]
    assert statuses == [200, 200, 429]
    _, payload, _ = _call(server, "GET", "/api/agent/status", jar)
    assert payload["signed_in"] is True and payload["owner"] is False
    assert payload["quota"]["remaining"] == 0


@pytest.mark.parametrize("credential", ["bad-aud", "bad-iss", "tok-old", "unverified", "forged"])
def test_rejected_tokens_get_401_and_no_session(live_agent, google, credential):
    server, _ = live_agent
    status, payload, session, _ = _sign_in(server, credential)
    assert status == 401 and session is None
    assert credential not in json.dumps(payload)


def test_tampered_session_cookie_is_just_a_visitor(live_agent, google, monkeypatch):
    server, calls = live_agent
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    _, _, session, _ = _sign_in(server, "visitor-cred")
    name, value = session.split("=", 1)
    version, body, mac = value.split(".")
    import base64

    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    data["e"] = "owner@example.com"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    jar = {"Cookie": f"{name}={version}.{forged}.{mac}"}
    _, payload, _ = _call(server, "GET", "/api/agent/status", jar)
    assert payload["signed_in"] is False and payload["owner"] is False
    assert _call(server, "POST", "/api/agent", jar, ASK)[0] == 200
    assert _call(server, "POST", "/api/agent", jar, ASK)[0] == 429


def test_sign_out_clears_cookie(live_agent, google):
    server, _ = live_agent
    _, _, session, _ = _sign_in(server, "owner-cred")
    status, payload, headers = _call(server, "POST", "/api/auth/logout", {"Cookie": session})
    assert status == 200 and payload["signed_in"] is False
    cleared = [c for c in headers["set-cookie-all"] if c.startswith("dia_session=")]
    assert cleared and cleared[0].startswith("dia_session=;") and "Max-Age=0" in cleared[0]
    # A browser drops the cookie; without it the next status is anonymous.
    _, payload, _ = _call(server, "GET", "/api/agent/status")
    assert payload["signed_in"] is False and payload["owner"] is False


def test_session_endpoint_requires_json_and_is_burst_limited(live_agent, google, monkeypatch):
    server, _ = live_agent
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request(
        "POST",
        "/api/auth/session",
        body="credential=owner-cred",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 415  # cross-site form posts can't log you in
    monkeypatch.setenv("DIA_AGENT_RATE_LIMIT", "2/60")
    statuses = [_sign_in(server, "forged")[0] for _ in range(3)]
    assert statuses == [401, 401, 429]


def test_status_reports_quota_and_sign_in(live_agent, google):
    server, _ = live_agent
    status, payload, headers = _call(server, "GET", "/api/agent/status")
    assert status == 200
    assert payload["auth"] == "google" and payload["owner"] is False
    assert payload["signed_in"] is False and payload["email"] is None
    assert payload["quota"]["limit"] == 10 and payload["quota"]["remaining"] == 10
    assert "set-cookie" in headers
