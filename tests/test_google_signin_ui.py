"""Ask DIA UI: daily-limit message and Google sign-in (static + Playwright)."""

import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_auth
import agent_quota
import app
from agent_quota import DailyQuota, InMemoryQuotaStore

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ID = "1234-test.apps.googleusercontent.com"
# Test-only signing key, not a real secret.
TEST_SESSION_KEY = "unit-test-session-signing-key-0123456789abcdef"
GIS_SRC = "https://accounts.google.com/gsi/client"

# Stand-in for Google Identity Services: renders a plain button that hands the
# page a fixed credential (window.__TEST_CRED) the mocked server verifier knows.
FAKE_GIS = """
window.__gisCalls = [];
window.google = { accounts: { id: {
  initialize(config) { window.__gisConfig = config; window.__gisCalls.push('initialize'); },
  renderButton(el, options) {
    window.__gisCalls.push('renderButton');
    const b = document.createElement('button');
    b.id = 'gisButton'; b.type = 'button'; b.textContent = 'Sign in with Google';
    b.onclick = () => window.__gisConfig.callback({ credential: window.__TEST_CRED || 'owner-cred' });
    el.append(b);
  },
  disableAutoSelect() { window.__gisCalls.push('disableAutoSelect'); },
} } };
"""


# --- static checks (always run) ------------------------------------------


def test_sign_in_script_is_served_and_loaded():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/google-signin.js"></script>' in html
    assert html.index("/static/app.js") < html.index("/static/google-signin.js")
    assert app.PUBLIC_FILES["/static/google-signin.js"].exists()
    assert "/static/owner-key.js" not in app.PUBLIC_FILES
    assert not (ROOT / "static" / "owner-key.js").exists()


def test_app_js_shows_server_error_text():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    # A 429's JSON "error" (the daily-limit message) is what Ask DIA displays.
    assert re.search(r'throw new Error\(payload\.error \|\| "Agent run failed"\)', js)
    assert "agentOutput.textContent = error.message" in js
    assert "DIA_OWNER" not in js


def test_sign_in_script_keeps_no_token_in_the_page():
    js = (ROOT / "static" / "google-signin.js").read_text(encoding="utf-8")
    assert "console." not in js
    assert "localStorage" not in js and "sessionStorage" not in js
    assert "disableAutoSelect" in js and "/api/auth/logout" in js


def test_no_owner_token_left_anywhere():
    for path in [ROOT / "app.py", ROOT / "agent_auth.py", ROOT / ".env.example", ROOT / "README.md"]:
        assert "DIA_OWNER_TOKEN" not in path.read_text(encoding="utf-8"), path


# --- browser check (skipped without Playwright/Chromium) -----------------


@pytest.fixture
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        candidates = [os.environ.get("DIA_CHROME"), None, "/usr/bin/google-chrome", "/usr/bin/chromium"]
        last_error = None
        for path in candidates:
            if path is not None and not os.path.exists(path):
                continue
            try:
                kwargs = {"args": ["--no-sandbox"]}
                if path:
                    kwargs["executable_path"] = path
                b = pw.chromium.launch(**kwargs)
                break
            except Exception as error:  # noqa: BLE001
                last_error = error
        else:
            pytest.skip(f"no Chromium available for Playwright: {last_error}")
        yield b
        b.close()


@pytest.fixture
def live_server(monkeypatch):
    import agent_runtime

    for name in (
        "DIA_AGENT_RATE_LIMIT",
        "K_SERVICE",
        "DIA_QUOTA_STORE",
        "MEMORY_SHEET_ID",
        "OBS_CAPTURE",
        "GOOGLE_CLIENT_ID",
        "OWNER_EMAILS",
        "DIA_SESSION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    monkeypatch.setattr(agent_runtime, "run_agent", lambda message: "fake agent reply")
    agent_auth.agent_rate_limiter.reset()
    app.status_rate_limiter.reset()
    app.auth_rate_limiter.reset()
    agent_quota.set_quota(DailyQuota(InMemoryQuotaStore()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    agent_quota.set_quota(None)


@pytest.fixture
def google_configured(monkeypatch):
    import time

    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DIA_SESSION_SECRET", TEST_SESSION_KEY)
    monkeypatch.setenv("OWNER_EMAILS", "owner@example.com")
    emails = {"owner-cred": "owner@example.com", "visitor-cred": "visitor@example.com"}

    def fake_verify(token, audience):
        if token not in emails or audience != CLIENT_ID:
            raise ValueError("bad token")
        return {
            "iss": "accounts.google.com",
            "aud": CLIENT_ID,
            "exp": time.time() + 3600,
            "sub": token,
            "email": emails[token],
            "email_verified": True,
        }

    monkeypatch.setattr(agent_auth, "google_verifier", fake_verify)


def _page(browser):
    page = browser.new_page()
    requested = []

    def gis(route):
        requested.append(route.request.url)
        route.fulfill(status=200, content_type="text/javascript", body=FAKE_GIS)

    page.route(GIS_SRC + "*", gis)
    return page, requested


def _ask(page, text):
    page.evaluate(
        """(text) => {
            document.querySelector('#agentMessage').value = text;
            document.querySelector('#runChat').click();
        }""",
        text,
    )


def _limit_hit(page):
    page.wait_for_function(
        "document.querySelector('#agentOutput').textContent.includes(\"You've used today's 1 question\")"
    )
    assert "Come back tomorrow" in page.text_content("#agentOutput")


def test_no_client_id_hides_button_and_still_caps(browser, live_server):
    page, requested = _page(browser)
    page.goto(live_server)
    page.wait_for_function("document.querySelector('#agentQuota')?.textContent.includes('1 of 1')")
    assert not page.is_visible("#diaAccount")
    assert requested == []  # GIS is never loaded
    _ask(page, "first question")
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")
    _ask(page, "second question")
    _limit_hit(page)
    page.close()


def test_google_button_visitor_cap_sign_out_and_owner(browser, live_server, google_configured):
    page, requested = _page(browser)
    page.goto(live_server)
    page.wait_for_selector("#gisButton", state="visible")
    assert requested and requested[0].startswith(GIS_SRC)
    assert page.evaluate("window.__gisConfig.client_id") == CLIENT_ID
    # The control sits in the top bar's right corner, just before the theme toggle.
    assert page.evaluate(
        "document.querySelector('#diaAccount').nextElementSibling.id === 'themeToggle'"
    )

    # A non-owner Google account is still capped at the daily limit.
    page.evaluate("window.__TEST_CRED = 'visitor-cred'")
    page.click("#gisButton")
    page.wait_for_selector("#diaSignOut", state="visible")
    assert page.text_content("#diaSignOut") == "Sign out"
    assert not page.is_visible("#gisButton")
    _ask(page, "first question")
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")
    _ask(page, "second question")
    _limit_hit(page)
    assert page.evaluate("document.cookie").find("dia_session") == -1  # HttpOnly

    # Sign out clears the session and turns off GIS auto-select.
    page.click("#diaSignOut")
    page.wait_for_selector("#gisButton", state="visible")
    assert "disableAutoSelect" in page.evaluate("window.__gisCalls")

    # The owner signs in and is unlimited.
    page.evaluate("window.__TEST_CRED = 'owner-cred'")
    page.click("#gisButton")
    page.wait_for_function("document.querySelector('#agentQuota').textContent === 'Owner: unlimited'")
    assert page.text_content("#diaSignOut") == "Owner · Sign out"
    _ask(page, "owner question")
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")
    page.close()
