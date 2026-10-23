"""agent_auth: Google ID-token checks, owner rule, signed session, client IP."""

from __future__ import annotations

import time

import pytest

import agent_auth
from agent_auth import (
    GoogleIdentity,
    Identity,
    RateLimiter,
    SignInError,
    client_ip,
    hashed_ip,
    is_owner_identity,
    make_session,
    owner_emails,
    read_session,
    verify_google_credential,
)
from lockdown_support import CLIENT_ID, OWNERS, TEST_SESSION_KEY, claims, gate_env  # noqa: F401


@pytest.fixture
def google(monkeypatch, gate_env):
    box = {"claims": claims()}
    monkeypatch.setattr(agent_auth, "google_verifier", lambda token, aud: dict(box["claims"]))
    return box


# --- kill switch --------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "maybe", "TRUEISH"])
def test_ask_dia_is_off_unless_explicitly_true(monkeypatch, value):
    monkeypatch.setenv("ASK_DIA_ENABLED", value)
    assert agent_auth.ask_dia_enabled() is False


def test_ask_dia_unset_is_off(monkeypatch):
    monkeypatch.delenv("ASK_DIA_ENABLED", raising=False)
    assert agent_auth.ask_dia_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_ask_dia_explicitly_on(monkeypatch, value):
    monkeypatch.setenv("ASK_DIA_ENABLED", value)
    assert agent_auth.ask_dia_enabled() is True


# --- ID token verification ------------------------------------------------------


def test_sign_in_needs_client_id_and_strong_secret(monkeypatch, gate_env):
    assert agent_auth.sign_in_enabled()
    monkeypatch.setenv("DIA_SESSION_SECRET", "short")
    assert not agent_auth.sign_in_enabled()
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    assert not agent_auth.sign_in_enabled()


def test_valid_token_returns_identity_keyed_on_sub(google):
    google["claims"] = claims(email=" Visitor@Example.COM ", sub="sub-123")
    identity = verify_google_credential("token")
    assert (identity.sub, identity.email, identity.email_verified) == ("sub-123", "visitor@example.com", True)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"iss": "https://evil.example.com"}, "issuer"),
        ({"aud": "someone-else.apps.googleusercontent.com"}, "another app"),
        ({"exp": time.time() - 3600}, "expired"),
        ({"exp": "soon"}, "expired"),
        ({"email": ""}, "no email"),
        ({"email_verified": False}, "not verified"),
        ({"email_verified": "false"}, "not verified"),
        ({"sub": ""}, "subject"),
    ],
)
def test_bad_claims_are_rejected(google, overrides, message):
    google["claims"] = claims(**overrides)
    with pytest.raises(SignInError, match=message):
        verify_google_credential("token")


def test_signature_failure_and_missing_token_are_rejected(google, monkeypatch):
    def boom(token, aud):
        raise ValueError("Could not verify token signature.")

    monkeypatch.setattr(agent_auth, "google_verifier", boom)
    with pytest.raises(SignInError, match="failed"):
        verify_google_credential("token")
    with pytest.raises(SignInError, match="Missing"):
        verify_google_credential("   ")
    with pytest.raises(SignInError, match="Missing"):
        verify_google_credential("x" * 5000)


def test_verifier_gets_our_client_id_as_audience(google, monkeypatch):
    seen = {}

    def spy(token, aud):
        seen["aud"] = aud
        return claims()

    monkeypatch.setattr(agent_auth, "google_verifier", spy)
    verify_google_credential("token")
    assert seen["aud"] == CLIENT_ID


def test_real_google_auth_rejects_garbage_token(gate_env, monkeypatch):
    pytest.importorskip("google.oauth2.id_token")
    monkeypatch.setattr(agent_auth, "google_verifier", agent_auth._google_verify)
    with pytest.raises(SignInError):
        verify_google_credential("not-a-jwt")


# --- owner rule (several owners, verified email only) ----------------------------


def test_owner_emails_parse_trim_and_lowercase(gate_env):
    assert owner_emails() == {"owner@example.com", "second.owner@example.com", "third@example.com"}


@pytest.mark.parametrize(
    "email", ["owner@example.com", "OWNER@EXAMPLE.COM", " Second.Owner@example.com ", "third@EXAMPLE.com"]
)
def test_every_listed_owner_is_owner_any_case(gate_env, email):
    assert is_owner_identity(Identity(sub="s", email=email, email_verified=True))


def test_unverified_email_is_never_owner(gate_env):
    assert not is_owner_identity(Identity(sub="s", email="owner@example.com", email_verified=False))


@pytest.mark.parametrize("email", ["visitor@example.com", "owner@example.co", "owner@example.com.evil", ""])
def test_unlisted_email_is_not_owner(gate_env, email):
    assert not is_owner_identity(Identity(sub="s", email=email, email_verified=True))


def test_no_owner_emails_means_no_owners(gate_env, monkeypatch):
    monkeypatch.setenv("OWNER_EMAILS", " , ,")
    assert owner_emails() == frozenset()
    assert not is_owner_identity(Identity(sub="s", email="owner@example.com", email_verified=True))


def test_no_real_addresses_are_hard_coded():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("agent_auth.py", "agent_quota.py", "app.py", ".env.example", "README.md"):
        text = (root / name).read_text(encoding="utf-8").lower()
        assert "gmail.com" not in text, name


# --- session cookie -------------------------------------------------------------


def test_session_round_trip_carries_sub_and_verified(gate_env):
    cookie = make_session(GoogleIdentity(email="owner@example.com", sub="sub-o"))
    identity = read_session(cookie)
    assert identity == Identity(sub="sub-o", email="owner@example.com", email_verified=True)
    assert identity.owner and identity.uncapped


def test_session_expires(gate_env):
    cookie = make_session(GoogleIdentity(email="a@example.com", sub="s"), now=lambda: 1000.0)
    ttl = agent_auth.session_ttl_seconds()
    assert read_session(cookie, now=lambda: 1000.0 + ttl - 1) is not None
    assert read_session(cookie, now=lambda: 1000.0 + ttl) is None


def test_session_ttl_is_clamped(monkeypatch):
    monkeypatch.setenv("DIA_SESSION_TTL_SECONDS", "10")
    assert agent_auth.session_ttl_seconds() == agent_auth.MIN_SESSION_TTL_SECONDS
    monkeypatch.setenv("DIA_SESSION_TTL_SECONDS", str(10**9))
    assert agent_auth.session_ttl_seconds() == agent_auth.MAX_SESSION_TTL_SECONDS
    monkeypatch.setenv("DIA_SESSION_TTL_SECONDS", "junk")
    assert agent_auth.session_ttl_seconds() == agent_auth.DEFAULT_SESSION_TTL_SECONDS


def test_tampered_resigned_or_foreign_sessions_are_rejected(gate_env, monkeypatch):
    import base64
    import hashlib
    import hmac
    import json

    cookie = make_session(GoogleIdentity(email="visitor@example.com", sub="sub-v"))
    version, body, mac = cookie.split(".")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    data["e"] = "owner@example.com"  # try to become owner
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    assert read_session(f"{version}.{forged}.{mac}") is None
    # Re-signed with a guessed key.
    guess = base64.urlsafe_b64encode(
        hmac.new(b"guess", f"{version}.{forged}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert read_session(f"{version}.{forged}.{guess}") is None
    assert read_session("v1." + body + "." + mac) is None
    assert read_session(cookie + ".extra") is None
    assert read_session("x" * 5000) is None
    # A session minted for another OAuth client is refused.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "other.apps.googleusercontent.com")
    assert read_session(cookie) is None


def test_unverified_flag_in_session_is_not_owner(gate_env):
    cookie = make_session(GoogleIdentity(email="owner@example.com", sub="s", email_verified=False))
    identity = read_session(cookie)
    assert identity is not None and not identity.owner and not identity.uncapped


def test_session_cookie_attributes(gate_env):
    header = agent_auth.session_cookie_header("abc", secure=True)
    for part in ("dia_session=abc", "HttpOnly", "SameSite=Lax", "Secure", "Path=/", "Max-Age=7200"):
        assert part in header
    assert "Secure" not in agent_auth.session_cookie_header("abc", secure=False)


def test_local_dev_identity_only_off_cloud_run(gate_env, monkeypatch):
    assert agent_auth.request_identity({}) is None
    monkeypatch.setenv("DIA_LOCAL_DEV_NO_SIGN_IN", "true")
    assert agent_auth.request_identity({}).local_dev
    monkeypatch.setenv("K_SERVICE", "dia")
    assert agent_auth.request_identity({}) is None


# --- client IP / hashing / burst ------------------------------------------------


def test_forwarded_for_ignored_off_cloud_run(gate_env):
    assert client_ip({"X-Forwarded-For": "1.2.3.4"}, "9.9.9.9") == "9.9.9.9"


def test_forwarded_for_uses_rightmost_entry_on_cloud_run(gate_env, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "dia")
    # The first hop is client-controlled; Google's front end appends the real one.
    assert client_ip({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}, "169.254.1.1") == "203.0.113.9"
    assert client_ip({"X-Forwarded-For": "  "}, "169.254.1.1") == "169.254.1.1"


def test_forwarded_for_trusted_when_opted_in(gate_env, monkeypatch):
    monkeypatch.setenv("DIA_TRUST_FORWARDED_FOR", "true")
    assert client_ip({"X-Forwarded-For": "6.6.6.6, 198.51.100.2"}, "10.0.0.1") == "198.51.100.2"


def test_hashed_ip_is_salted_and_groups_ipv6_slash_64(gate_env, monkeypatch):
    a = hashed_ip("2001:db8:1:2::1")
    assert a == hashed_ip("2001:db8:1:2:ffff::9") != hashed_ip("2001:db8:1:3::1")
    assert "2001" not in a and a.startswith("ip:")
    monkeypatch.setenv("DIA_VISITOR_SALT", "another-salt")
    assert hashed_ip("2001:db8:1:2::1") != a


def test_rate_limiter_sliding_window(monkeypatch):
    now = [0.0]
    monkeypatch.setenv("TEST_LIMIT", "3/10")
    limiter = RateLimiter("TEST_LIMIT", "3/10", clock=lambda: now[0])
    assert [limiter.check("k")[0] for _ in range(4)] == [True, True, True, False]
    assert limiter.check("other")[0]
    now[0] = 10.1
    assert limiter.check("k")[0]


def test_rate_limiter_disabled_and_bad_config(monkeypatch):
    limiter = RateLimiter("TEST_LIMIT", "1/10", clock=lambda: 0.0)
    monkeypatch.setenv("TEST_LIMIT", "off")
    assert all(limiter.check("k")[0] for _ in range(20))
    monkeypatch.setenv("TEST_LIMIT", "nonsense")
    assert limiter.check("k")[0] and not limiter.check("k")[0]
