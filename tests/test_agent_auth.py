"""Owner sign-in, client IP and burst guard for /api/agent (agent_auth.py)."""

import pytest

from agent_auth import (
    MIN_OWNER_TOKEN_LENGTH,
    RateLimiter,
    client_ip,
    configured_mode,
    is_owner,
    supplied_owner_token,
)

OWNER = "unit-test-owner-key-not-a-secret-0123456789"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DIA_OWNER_TOKEN", "K_SERVICE", "DIA_TRUST_FORWARDED_FOR", "DIA_AGENT_RATE_LIMIT"):
        monkeypatch.delenv(name, raising=False)


# --- owner key ------------------------------------------------------------


def test_owner_key_valid_invalid_missing(monkeypatch):
    monkeypatch.setenv("DIA_OWNER_TOKEN", OWNER)
    assert configured_mode() == "owner-key"
    assert is_owner({"Authorization": f"Bearer {OWNER}"})
    assert is_owner({"X-DIA-Owner-Token": OWNER})
    assert not is_owner({"Authorization": "Bearer wrong-key"})
    assert not is_owner({"Authorization": OWNER})  # no Bearer scheme
    assert not is_owner({})


def test_no_owner_configured_means_nobody_is_owner():
    assert configured_mode() == "visitors-only"
    assert not is_owner({"Authorization": "Bearer anything-at-all-anything-at-all-xx"})
    assert not is_owner({"Authorization": "Bearer "})


def test_short_owner_key_is_ignored(monkeypatch):
    short = "x" * (MIN_OWNER_TOKEN_LENGTH - 1)
    monkeypatch.setenv("DIA_OWNER_TOKEN", short)
    assert configured_mode() == "visitors-only"
    assert not is_owner({"Authorization": f"Bearer {short}"})


def test_supplied_owner_token_prefers_explicit_header():
    assert supplied_owner_token({"X-DIA-Owner-Token": " a ", "Authorization": "Bearer b"}) == "a"
    assert supplied_owner_token({"Authorization": "bearer b"}) == "b"
    assert supplied_owner_token({"Authorization": "Basic abc"}) == ""


# --- client_ip() ----------------------------------------------------------


def test_forwarded_for_ignored_off_cloud_run():
    assert client_ip({"X-Forwarded-For": "1.2.3.4"}, "127.0.0.1") == "127.0.0.1"


def test_forwarded_for_uses_rightmost_entry_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "svc")
    headers = {"X-Forwarded-For": "6.6.6.6, 198.51.100.9"}  # client-forged, real
    assert client_ip(headers, "169.254.1.1") == "198.51.100.9"
    assert client_ip({}, "169.254.1.1") == "169.254.1.1"


def test_forwarded_for_trusted_when_opted_in(monkeypatch):
    monkeypatch.setenv("DIA_TRUST_FORWARDED_FOR", "1")
    assert client_ip({"X-Forwarded-For": "9.9.9.9, 10.0.0.2"}, "127.0.0.1") == "10.0.0.2"


# --- burst guard ----------------------------------------------------------


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
