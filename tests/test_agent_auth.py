"""Google sign-in sessions, client IP and burst guard (agent_auth.py)."""

import base64
import json
import time

import pytest

import agent_auth
from agent_auth import (
    GoogleIdentity,
    RateLimiter,
    SESSION_TTL_SECONDS,
    SignInError,
    client_ip,
    configured_mode,
    is_owner,
    is_owner_email,
    make_session,
    read_session,
    session_cookie_header,
    verify_google_credential,
)

CLIENT_ID = "1234-test.apps.googleusercontent.com"
# Test-only signing key, not a real secret.
TEST_SESSION_KEY = "unit-test-session-signing-key-0123456789abcdef"


def claims(**overrides):
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 3600,
        "iat": time.time(),
        "sub": "1000001",
        "email": "Owner@Example.com",
        "email_verified": True,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "GOOGLE_CLIENT_ID",
        "OWNER_EMAILS",
        "DIA_SESSION_SECRET",
        "K_SERVICE",
        "DIA_TRUST_FORWARDED_FOR",
        "DIA_AGENT_RATE_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def google(monkeypatch):
    """Sign-in configured, with a mocked Google verifier keyed by token string."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    monkeypatch.setenv("OWNER_EMAILS", " owner@example.com , second@example.org")
    tokens = {}

    def fake_verify(token, audience):
        assert audience == CLIENT_ID
        if token not in tokens:
            raise ValueError("Could not verify token signature.")
        return tokens[token]

    monkeypatch.setattr(agent_auth, "google_verifier", fake_verify)
    return tokens


# --- configuration ----------------------------------------------------------


def test_sign_in_needs_client_id_and_strong_secret(monkeypatch):
    assert configured_mode() == "visitors-only"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    assert configured_mode() == "visitors-only"  # no secret
    monkeypatch.setenv("DIA_SESSION_SECRET", "too-short")
    assert configured_mode() == "visitors-only"
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    assert configured_mode() == "google"


def test_owner_emails_are_case_insensitive(monkeypatch):
    monkeypatch.setenv("OWNER_EMAILS", "Owner@Example.com, ,x@y.z")
    assert is_owner_email("owner@example.com")
    assert is_owner_email(" OWNER@EXAMPLE.COM ")
    assert not is_owner_email("someone@example.com")
    assert not is_owner_email(None) and not is_owner_email("")


# --- ID token verification ----------------------------------------------------


def test_valid_token_returns_lowercased_identity(google):
    google["good"] = claims()
    identity = verify_google_credential("good")
    assert identity.email == "owner@example.com" and identity.sub == "1000001"
    google["plain-iss"] = claims(iss="accounts.google.com")
    assert verify_google_credential("plain-iss").email == "owner@example.com"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"aud": "someone-else.apps.googleusercontent.com"}, "another app"),
        ({"iss": "https://evil.example.com"}, "issuer"),
        ({"exp": time.time() - 3600}, "expired"),
        ({"email_verified": False}, "not verified"),
        ({"email_verified": "false"}, "not verified"),
        ({"email": ""}, "no email"),
        ({"sub": ""}, "subject"),
    ],
)
def test_bad_claims_are_rejected(google, overrides, message):
    google["tok"] = claims(**overrides)
    with pytest.raises(SignInError, match=message):
        verify_google_credential("tok")


def test_verifier_failure_and_missing_token_are_rejected(google):
    with pytest.raises(SignInError, match="failed"):
        verify_google_credential("forged-or-expired")  # verifier raises
    with pytest.raises(SignInError, match="Missing"):
        verify_google_credential("   ")


def test_not_configured_rejects_everything(monkeypatch):
    monkeypatch.setattr(agent_auth, "google_verifier", lambda t, a: claims())
    with pytest.raises(SignInError, match="not configured"):
        verify_google_credential("anything")


def test_real_google_auth_rejects_garbage_token(monkeypatch):
    """Production verifier (google-auth) refuses a non-JWT without network."""
    from google.oauth2 import id_token

    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(id_token, "_fetch_certs", lambda request, url: {})  # no network
    with pytest.raises(SignInError):
        verify_google_credential("not-a-jwt")


# --- signed session cookie ----------------------------------------------------


def test_session_round_trip_and_expiry(google):
    now = [1_800_000_000.0]
    value = make_session(GoogleIdentity("owner@example.com", "1"), now=lambda: now[0])
    assert read_session(value, now=lambda: now[0]) == "owner@example.com"
    now[0] += SESSION_TTL_SECONDS
    assert read_session(value, now=lambda: now[0]) is None


def test_tampered_or_resigned_session_is_rejected(google, monkeypatch):
    value = make_session(GoogleIdentity("visitor@example.com", "2"))
    version, body, mac = value.split(".")
    forged_body = base64.urlsafe_b64encode(
        json.dumps({"e": "owner@example.com", "s": "2", "iat": 0, "exp": 9_999_999_999}).encode()
    ).rstrip(b"=").decode()
    assert read_session(f"{version}.{forged_body}.{mac}") is None
    assert read_session(f"{version}.{body}.{mac[:-2]}xx") is None
    assert read_session("garbage") is None and read_session("") is None
    assert read_session(value) == "visitor@example.com"
    # Rotating the secret signs everyone out.
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY[::-1])
    assert read_session(value) is None


def test_owner_session_and_non_owner_session(google):
    owner = make_session(GoogleIdentity("owner@example.com", "1"))
    other = make_session(GoogleIdentity("visitor@example.com", "2"))
    assert is_owner({"Cookie": f"dia_session={owner}"})
    assert not is_owner({"Cookie": f"dia_session={other}"})
    assert not is_owner({})


def test_session_cookie_attributes():
    cookie = session_cookie_header("v1.x.y", secure=True)
    for part in ("dia_session=v1.x.y", "HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
        assert part in cookie
    assert f"Max-Age={12 * 3600}" in cookie
    assert "Secure" not in session_cookie_header("v", secure=False)


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
