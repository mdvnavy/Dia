"""Lockdown v2: "No use without signing in ... same as Tia".

Signed-out visitors may read the pages; every functional route answers
401 {"error": "sign_in_required"} before doing any work.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

import agent_auth
import app
from lockdown_support import TEST_SESSION_KEY, gate_env, server  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

# The only routes allowed to work signed out. Adding to this list is a
# product decision ("read and peruse"), not a refactor.
PUBLIC_GET = {"/healthz", "/health", "/api/config", "/api/auth/config", "/api/agent/status"}
PUBLIC_POST = {"/api/auth/session", "/api/auth/logout"}

GATED = sorted(
    [("GET", path) for path, (_, gated) in app.GET_ROUTES.items() if gated]
    + [("POST", path) for path, (_, gated) in app.POST_ROUTES.items() if gated]
)

BODIES = {
    "/api/process": {"questionnaire": "Company: Example\nProblem: slow intake"},
    "/api/agent": {"message": "hello"},
    "/api/pdf": {"questionnaire": "Company: Example"},
    "/api/extract": None,
}


def test_audited_route_list_is_exactly_this():
    # The audited table. Update deliberately, with a test for any new route.
    assert GATED == [
        ("GET", "/api/sample"),
        ("GET", "/api/usage"),
        ("POST", "/api/agent"),
        ("POST", "/api/extract"),
        ("POST", "/api/pdf"),
        ("POST", "/api/process"),
    ]
    assert {p for p, (_, g) in app.GET_ROUTES.items() if not g} == PUBLIC_GET
    assert {p for p, (_, g) in app.POST_ROUTES.items() if not g} == PUBLIC_POST


def test_every_post_route_goes_through_the_table():
    """Fails if a POST (or GET API) route is added outside the gated table."""
    source = inspect.getsource(app.ClientDiscoveryHandler.do_POST)
    tree = ast.parse(textwrap.dedent(source))
    # do_POST may only dispatch via POST_ROUTES; no path literal comparisons.
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in literals if s.startswith("/")], literals
    assert "POST_ROUTES" in source and "_dispatch" in source
    get_source = inspect.getsource(app.ClientDiscoveryHandler.do_GET)
    assert not re.search(r'path\s*==\s*"/api', get_source)
    assert not re.search(r'path\s*(==|in)\s*[\(\[{]?\s*"/api', get_source)
    # Every handle_* method is reachable only from the table (plus helpers).
    handlers = {name for name in vars(app.ClientDiscoveryHandler) if name.startswith("handle_")}
    routed = {h for h, _ in app.GET_ROUTES.values()} | {h for h, _ in app.POST_ROUTES.values()}
    assert handlers == routed, handlers ^ routed
    # The whole module: no ad-hoc self.path comparisons anywhere.
    module = (ROOT / "app.py").read_text(encoding="utf-8")
    assert not re.search(r'self\.path\s*==', module)
    assert not re.search(r'\bpath\s*==\s*"/', module)


def test_every_public_route_is_on_the_allow_list():
    for path, (_, gated) in {**app.GET_ROUTES, **app.POST_ROUTES}.items():
        if not gated:
            assert path in PUBLIC_GET | PUBLIC_POST, path


@pytest.mark.parametrize("method,path", GATED)
@pytest.mark.parametrize(
    "cookie",
    [
        None,
        "dia_session=",
        "dia_session=garbage",
        "dia_session=v2.e30.AAAA",
        "dia_vid=abc; other=1",
    ],
    ids=["no-cookie", "empty", "garbage", "forged-mac", "other-cookies"],
)
def test_signed_out_gets_401_before_any_work(server, gate_env, monkeypatch, method, path, cookie):
    import agent_quota
    import client_discovery.extract as extract

    touched = []
    monkeypatch.setattr(app, "build_intake_response", lambda *a, **k: touched.append("process"))
    monkeypatch.setattr(extract, "extract_text", lambda *a, **k: touched.append("extract"))
    monkeypatch.setattr(app, "read_sample_questionnaire", lambda: touched.append("sample"))
    quota = agent_quota.get_quota()
    monkeypatch.setattr(quota, "consume", lambda *a, **k: touched.append("quota"))
    monkeypatch.setattr(quota, "usage", lambda *a, **k: touched.append("usage"))
    headers = {
        # Spoofed "owner" signals must not help.
        "X-Owner-Email": "owner@example.com",
        "X-Forwarded-For": "127.0.0.1",
        "Authorization": "Bearer owner-cred",
    }
    if path == "/api/extract":
        status, hdrs, payload = server.request(
            method, path, raw=b"hello", headers={**headers, "X-Filename": "a.txt"}, cookie=cookie
        )
    else:
        body = BODIES.get(path)
        if body is not None:
            body = {**body, "email": "owner@example.com", "owner": True}
        status, hdrs, payload = server.request(method, path, body, headers=headers, cookie=cookie)
    assert status == 401
    assert payload["error"] == "sign_in_required"
    assert payload["message"] == agent_auth.SIGN_IN_MESSAGE
    assert hdrs.get("Cache-Control") == "no-store"
    assert touched == [] and gate_env == []


@pytest.mark.parametrize("method,path", GATED)
def test_signed_in_passes_the_gate(server, method, path):
    cookie, _ = server.sign_in()
    if path == "/api/extract":
        status, _, _ = server.request(
            method, path, raw=b"Company: Example", headers={"X-Filename": "a.txt"}, cookie=cookie
        )
    else:
        status, _, _ = server.request(method, path, BODIES.get(path), cookie=cookie)
    assert status == 200


@pytest.mark.parametrize(
    "path",
    ["/", "/how-it-works", "/healthz", "/health", "/api/config", "/api/auth/config",
     "/api/agent/status", "/static/style.css", "/static/app.js", "/static/google-signin.js"],
)
def test_read_only_pages_work_signed_out(server, path):
    if path not in app.PUBLIC_FILES and path not in app.GET_ROUTES:
        pytest.skip(f"{path} is not served on this branch")
    status, _, _ = server.request("GET", path)
    assert status == 200


def test_static_pages_and_business_card_link_are_public(server):
    pages = [p for p in app.PUBLIC_FILES if not p.startswith("/static/")]
    assert "/" in pages
    for path in pages:
        status, _, _ = server.request("GET", path)
        assert status == 200, path


def test_agent_status_is_public_but_carries_no_counts(server):
    status, _, payload = server.request("GET", "/api/agent/status")
    assert status == 200
    assert not {"usage", "remaining", "limit", "generations", "ask"} & set(payload)


def test_forged_and_expired_cookies_are_refused(server, monkeypatch):
    import time as _time

    cookie, _ = server.sign_in()
    value = cookie.split("=", 1)[1]
    version, body, mac = value.split(".")
    # Flip a character in the body (e.g. change the sub/email) -> MAC mismatch.
    tampered = f"{version}.{body[:-2]}{'A' if body[-2] != 'A' else 'B'}{body[-1]}.{mac}"
    for bad in (
        f"dia_session={tampered}",
        f"dia_session={version}.{body}.{mac[:-1]}x",
        f"dia_session=v1.{body}.{mac}",
    ):
        status, _, payload = server.request("GET", "/api/usage", cookie=bad)
        assert (status, payload["error"]) == (401, "sign_in_required"), bad
    # Expired: a correctly signed cookie whose exp has passed.
    old = agent_auth.make_session(
        agent_auth.GoogleIdentity(email="visitor@example.com", sub="sub-visitor"),
        now=lambda: _time.time() - 3 * 3600,
    )
    status, _, _ = server.request("GET", "/api/usage", cookie=f"dia_session={old}")
    assert status == 401
    status, _, _ = server.request("GET", "/api/usage", cookie=cookie)
    assert status == 200


def test_cookie_signed_with_another_secret_is_refused(server, monkeypatch):
    monkeypatch.setenv("DIA_SESSION_SECRET", "another-test-only-key-0123456789abcdefghij")
    cookie, _ = server.sign_in()
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    status, _, _ = server.request("GET", "/api/usage", cookie=cookie)
    assert status == 401


def test_local_dev_bypass_is_ignored_on_cloud_run(server, monkeypatch):
    monkeypatch.setenv("DIA_LOCAL_DEV_NO_SIGN_IN", "true")
    status, _, _ = server.request("GET", "/api/usage")
    assert status == 200  # local only
    monkeypatch.setenv("K_SERVICE", "dia-test")
    status, _, payload = server.request("GET", "/api/usage")
    assert (status, payload["error"]) == (401, "sign_in_required")


def test_kill_switch_comes_before_the_gate(server, monkeypatch, gate_env):
    monkeypatch.setenv("ASK_DIA_ENABLED", "false")
    status, hdrs, payload = server.request("POST", "/api/agent", {"message": "hi"})
    assert status == 503 and payload["enabled"] is False
    assert hdrs["Retry-After"] == "3600"
    cookie, _ = server.sign_in("owner-cred")
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 503 and gate_env == []


def test_auth_endpoints_burst_limit_on_salted_ip_hash(server, monkeypatch):
    monkeypatch.setenv("DIA_AUTH_RATE_LIMIT", "2/60")
    monkeypatch.setenv("K_SERVICE", "dia-test")
    agent_auth.reset_limiters()
    statuses = []
    for i in range(3):
        # Rotating a forged first XFF hop doesn't get a fresh allowance.
        status, _, _ = server.request(
            "POST", "/api/auth/session", {"credential": "visitor-cred"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        )
        statuses.append(status)
    assert statuses == [200, 200, 429]
