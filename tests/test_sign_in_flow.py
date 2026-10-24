"""Sign-in end to end with REAL google-auth verification (Tia's verifier, ported).

A locally generated RSA key signs test ID tokens; a stub transport serves the
matching public key where google-auth fetches Google's certs, in both formats
Google publishes (x509 map at /oauth2/v1/certs, and a JWKS). Nothing reaches
the network. All addresses are example.com.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

import agent_auth
import app
from lockdown_support import CLIENT_ID, Server, gate_env  # noqa: F401

KID = "test-kid-1"
QUESTIONNAIRE = {"questionnaire": "Company: Example Co\nProblem: intake takes too long"}


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, pem, cert.public_bytes(serialization.Encoding.PEM).decode()


GOOD_KEY, GOOD_PEM, GOOD_CERT = _keypair()
_, EVIL_PEM, _ = _keypair()


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


JWKS = {
    "keys": [
        {
            "kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
            "n": _b64u(GOOD_KEY.public_key().public_numbers().n),
            "e": _b64u(GOOD_KEY.public_key().public_numbers().e),
        }
    ]
}


class _Resp:
    def __init__(self, body: dict) -> None:
        self.status = 200
        self.headers = {"content-type": "application/json"}
        self.data = json.dumps(body).encode()


class StubGoogleCerts:
    """google.auth.transport.Request stand-in serving our public key."""

    def __init__(self, fmt: str = "x509") -> None:
        self.fmt = fmt
        self.urls: list[str] = []

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.urls.append(url)
        return _Resp(JWKS if self.fmt == "jwks" else {KID: GOOD_CERT})


def mint(pem: bytes = GOOD_PEM, **overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID,
        "sub": "1000001", "email": "visitor@example.com", "email_verified": True,
        "name": "Test Visitor", "iat": now, "exp": now + 3600,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    signer = crypt.RSASigner.from_string(pem, key_id=KID)
    return jwt.encode(signer, payload).decode()


@pytest.fixture(params=["x509", "jwks"])
def real_google(request, gate_env, monkeypatch):
    """Real google-auth verification against the stub key endpoint."""
    stub = StubGoogleCerts(request.param)
    monkeypatch.setattr(agent_auth, "google_verifier", agent_auth._google_verify)
    monkeypatch.setattr(agent_auth, "google_request_adapter", stub)
    monkeypatch.setenv("OWNER_EMAILS", "owner@example.com, second.owner@example.com")
    return stub


@pytest.fixture
def srv(real_google):
    s = Server()
    yield s
    s.close()


def sign_in(srv, token, via="header"):
    if via == "header":
        return srv.request("POST", "/api/auth/session", {}, headers={"Authorization": f"Bearer {token}"})
    return srv.request("POST", "/api/auth/session", {"credential": token})


def cookie_of(headers):
    return headers["Set-Cookie"].split(";", 1)[0]


# --- token verification (backend) ------------------------------------------------


def test_valid_token_verifies_against_google_certs_url(real_google):
    identity = agent_auth.verify_google_credential(mint())
    assert (identity.sub, identity.email, identity.email_verified) == ("1000001", "visitor@example.com", True)
    assert real_google.urls == ["https://www.googleapis.com/oauth2/v1/certs"]


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"aud": "someone-else.apps.googleusercontent.com"}, "failed"),
        ({"iss": "https://evil.example.com"}, "failed"),
        ({"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}, "expired"),
        ({"email_verified": False}, "not verified"),
        ({"sub": None}, "failed"),
        ({"email": None}, "failed"),
    ],
    ids=["wrong-aud", "wrong-iss", "expired", "email-unverified", "missing-sub", "missing-email"],
)
def test_bad_tokens_are_rejected(real_google, overrides, message):
    with pytest.raises(agent_auth.SignInError, match=message):
        agent_auth.verify_google_credential(mint(**overrides))


def test_bad_signature_is_rejected(real_google):
    forged = mint(pem=EVIL_PEM)  # same kid, attacker's key
    with pytest.raises(agent_auth.SignInError, match="failed"):
        agent_auth.verify_google_credential(forged)
    header, body, sig = mint().split(".")
    tampered_body = base64.urlsafe_b64encode(
        json.dumps({**json.loads(base64.urlsafe_b64decode(body + "==")), "email": "owner@example.com"}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(agent_auth.SignInError):
        agent_auth.verify_google_credential(f"{header}.{tampered_body}.{sig}")
    for junk in ("not-a-jwt", "a.b.c", "Bearer ", ""):
        with pytest.raises(agent_auth.SignInError):
            agent_auth.verify_google_credential(junk)


def test_tia_error_classes_are_raised(real_google):
    with pytest.raises(agent_auth.InvalidAudienceError):
        agent_auth._google_verify(mint(aud="x.apps.googleusercontent.com"), CLIENT_ID)
    with pytest.raises(agent_auth.ExpiredTokenError):
        agent_auth._google_verify(mint(iat=1, exp=2), CLIENT_ID)
    with pytest.raises(agent_auth.UnverifiedEmailError):
        agent_auth._google_verify(mint(email_verified=False), CLIENT_ID)
    with pytest.raises(agent_auth.TamperedTokenError):
        agent_auth._google_verify(mint(pem=EVIL_PEM), CLIENT_ID)
    with pytest.raises(agent_auth.MissingTokenError):
        agent_auth._google_verify("Bearer  ", CLIENT_ID)
    assert agent_auth._google_verify("Bearer " + mint(), CLIENT_ID)["sub"] == "1000001"


# --- HTTP flow --------------------------------------------------------------------


@pytest.mark.parametrize("via", ["header", "body"])
def test_valid_sign_in_sets_session_cookie_with_tia_shape(srv, via):
    status, headers, payload = sign_in(srv, mint(), via)
    assert status == 200
    assert {k: payload[k] for k in ("status", "authorized", "signed_in", "owner")} == {
        "status": "authorized", "authorized": True, "signed_in": True, "owner": False,
    }
    assert payload["user"] == {"sub": "1000001", "email": "visitor@example.com", "name": "Test Visitor"}
    cookie = headers["Set-Cookie"]
    for part in ("dia_session=v2.", "HttpOnly", "SameSite=Lax", "Path=/"):
        assert part in cookie
    assert mint()[:20] not in json.dumps(payload)  # the ID token is never echoed


@pytest.mark.parametrize(
    "overrides",
    [{"aud": "other.apps.googleusercontent.com"}, {"iss": "https://evil.example.com"},
     {"iat": 1, "exp": 2}, {"email_verified": False}, {"sub": None}],
    ids=["wrong-aud", "wrong-iss", "expired", "unverified", "missing-sub"],
)
def test_bad_token_sign_in_sets_no_cookie(srv, overrides):
    status, headers, payload = sign_in(srv, mint(**overrides))
    assert status == 401 and "Set-Cookie" not in headers
    assert payload["authorized"] is False and payload["status"] == "unauthorized"


def test_bad_signature_sign_in_sets_no_cookie(srv):
    status, headers, _ = sign_in(srv, mint(pem=EVIL_PEM))
    assert status == 401 and "Set-Cookie" not in headers


def test_cookie_unlocks_process_and_agent(srv):
    _, headers, _ = sign_in(srv, mint())
    cookie = cookie_of(headers)
    status, _, payload = srv.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    assert status == 200 and payload["usage"]["generations"]["remaining"] == 9
    status, _, payload = srv.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 200 and payload["reply"] == "fake agent reply"


def test_raw_id_token_alone_does_not_unlock(srv):
    token = mint()
    for path, body in (("/api/process", QUESTIONNAIRE), ("/api/agent", {"message": "hi"})):
        status, _, payload = srv.request("POST", path, body, headers={"Authorization": f"Bearer {token}"})
        assert (status, payload["error"]) == (401, "sign_in_required")


@pytest.mark.parametrize("path,body", [("/api/process", QUESTIONNAIRE), ("/api/agent", {"message": "hi"})])
def test_missing_tampered_or_expired_cookie_gets_401(srv, path, body):
    _, headers, _ = sign_in(srv, mint())
    version, payload_b64, mac = cookie_of(headers).split("=", 1)[1].split(".")
    expired = agent_auth.make_session(
        agent_auth.GoogleIdentity(email="visitor@example.com", sub="1000001"), now=lambda: time.time() - 86400
    )
    flipped = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    for cookie in (None, f"dia_session={version}.{flipped}.{mac}", f"dia_session={expired}", "dia_session="):
        status, _, payload = srv.request("POST", path, body, cookie=cookie)
        assert (status, payload["error"]) == (401, "sign_in_required"), cookie


def test_sign_out_clears_the_cookie(srv):
    _, headers, _ = sign_in(srv, mint())
    cookie = cookie_of(headers)
    status, out_headers, payload = srv.request("POST", "/api/auth/logout", cookie=cookie)
    assert status == 200 and payload["signed_in"] is False
    cleared = out_headers["Set-Cookie"]
    assert cleared.startswith("dia_session=;") and "Max-Age=0" in cleared
    # A browser honouring that header has no session left.
    status, _, _ = srv.request("POST", "/api/process", QUESTIONNAIRE, cookie=cleared.split(";", 1)[0])
    assert status == 401
    _, _, cfg = srv.request("GET", "/api/auth/config")
    assert cfg["signed_in"] is False


@pytest.mark.parametrize("email", ["owner@example.com", "SECOND.Owner@Example.com"])
def test_owner_from_owner_emails_is_uncapped(srv, gate_env, email):
    _, headers, payload = sign_in(srv, mint(sub="2000002", email=email))
    assert payload["owner"] is True
    cookie = cookie_of(headers)
    for _ in range(12):
        assert srv.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0] == 200
    for _ in range(7):
        status, _, body = srv.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
        assert status == 200 and body["usage"]["unlimited"] is True


def test_unverified_owner_email_never_signs_in(srv):
    status, headers, _ = sign_in(srv, mint(sub="2000003", email="owner@example.com", email_verified=False))
    assert status == 401 and "Set-Cookie" not in headers


def test_non_owner_hits_ten_and_five(srv):
    _, headers, _ = sign_in(srv, mint())
    cookie = cookie_of(headers)
    gen = [srv.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0] for _ in range(11)]
    ask = [srv.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)[0] for _ in range(6)]
    assert gen == [200] * 10 + [429]
    assert ask == [200] * 5 + [429]


def test_limits_are_keyed_on_sub_not_email_or_ip(srv, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "dia-test")  # so X-Forwarded-For is read at all
    _, h1, _ = sign_in(srv, mint(sub="3000001", email="shared@example.com"))
    a = cookie_of(h1)
    for i in range(5):
        srv.request("POST", "/api/agent", {"message": "hi"}, cookie=a,
                    headers={"X-Forwarded-For": f"198.51.100.{i}"})
    # Same sub, new email (address changed at Google), new IP: still spent.
    _, h2, _ = sign_in(srv, mint(sub="3000001", email="renamed@example.com"),)
    status, _, _ = srv.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie_of(h2),
                               headers={"X-Forwarded-For": "203.0.113.200"})
    assert status == 429
    # Different sub, same email, same IP: its own fresh allowance.
    _, h3, _ = sign_in(srv, mint(sub="3000002", email="shared@example.com"))
    status, _, payload = srv.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie_of(h3),
                                     headers={"X-Forwarded-For": "198.51.100.0"})
    assert status == 200 and payload["usage"]["ask"]["remaining"] == 4


# --- config: fail closed ------------------------------------------------------------


GATED = [(m, p) for m, routes in (("GET", app.GET_ROUTES), ("POST", app.POST_ROUTES))
         for p, (_, gated) in routes.items() if gated]


@pytest.mark.parametrize("missing", ["GOOGLE_CLIENT_ID", "DIA_SESSION_SECRET"])
def test_missing_config_fails_closed(srv, monkeypatch, missing):
    _, headers, _ = sign_in(srv, mint())
    cookie = cookie_of(headers)  # valid while configured
    monkeypatch.delenv(missing)
    for method, path in GATED:
        body = QUESTIONNAIRE if method == "POST" else None
        status, _, payload = srv.request(method, path, body, cookie=cookie)
        assert (status, payload["error"]) == (401, "sign_in_required"), path
    status, headers, _ = sign_in(srv, mint())
    assert status == 404 and "Set-Cookie" not in headers
    _, _, cfg = srv.request("GET", "/api/auth/config")
    assert cfg["enabled"] is False and cfg["google_client_id"] is None


def test_weak_session_secret_fails_closed(srv, monkeypatch):
    monkeypatch.setenv("DIA_SESSION_SECRET", "too-short")
    status, _, _ = sign_in(srv, mint())
    assert status == 404
    assert srv.request("POST", "/api/process", QUESTIONNAIRE)[0] == 401
