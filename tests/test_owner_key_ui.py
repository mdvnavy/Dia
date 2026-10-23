"""Ask DIA UI: daily-limit message and owner sign-in (static + Playwright)."""

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
OWNER = "unit-test-owner-key-not-a-secret-0123456789"


# --- static checks (always run) ------------------------------------------


def test_owner_key_script_is_served_and_loaded():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/owner-key.js"></script>' in html
    assert html.index("/static/app.js") < html.index("/static/owner-key.js")
    assert app.PUBLIC_FILES["/static/owner-key.js"].exists()


def test_app_js_shows_server_error_text_and_sends_owner_header():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    # A 429's JSON "error" (the daily-limit message) is what Ask DIA displays.
    assert re.search(r'throw new Error\(payload\.error \|\| "Agent run failed"\)', js)
    assert "agentOutput.textContent = error.message" in js
    assert "window.DIA_OWNER.authHeaders()" in js


def test_owner_key_script_never_logs_or_echoes_the_key():
    js = (ROOT / "static" / "owner-key.js").read_text(encoding="utf-8")
    assert "console." not in js
    assert 'type="password"' in js
    assert "localStorage" in js and "Authorization" in js


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

    for name in ("DIA_AGENT_RATE_LIMIT", "K_SERVICE", "DIA_QUOTA_STORE", "MEMORY_SHEET_ID", "OBS_CAPTURE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("DIA_AGENT_DAILY_LIMIT", "1")
    monkeypatch.setenv("DIA_OWNER_TOKEN", OWNER)
    monkeypatch.setattr(agent_runtime, "run_agent", lambda message: "fake agent reply")
    agent_auth.agent_rate_limiter.reset()
    app.status_rate_limiter.reset()
    agent_quota.set_quota(DailyQuota(InMemoryQuotaStore()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    agent_quota.set_quota(None)


def _ask(page, text):
    page.evaluate(
        """(text) => {
            document.querySelector('#agentMessage').value = text;
            document.querySelector('#runChat').click();
        }""",
        text,
    )


def test_daily_limit_message_and_owner_sign_in_in_browser(browser, live_server):
    page = browser.new_page()
    page.goto(live_server)
    page.wait_for_function("document.querySelector('#agentQuota')?.textContent.includes('1 of 1')")

    _ask(page, "first question")
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")

    _ask(page, "second question")
    page.wait_for_function(
        "document.querySelector('#agentOutput').textContent.includes(\"You've used today's 1 question\")"
    )
    assert "Come back tomorrow" in page.text_content("#agentOutput")
    page.wait_for_function("document.querySelector('#agentQuota').textContent.includes('used')")

    # Owner signs in once; the key is stored locally and lifts the limit.
    page.evaluate(
        """(key) => {
            const input = document.querySelector('#ownerKeyInput');
            input.value = key;
            document.querySelector('#ownerKeyForm').requestSubmit();
        }""",
        OWNER,
    )
    page.wait_for_function("document.querySelector('#agentQuota').textContent === 'Owner: unlimited'")
    assert page.input_value("#ownerKeyInput") == ""  # not echoed back
    _ask(page, "owner question")
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")

    # A wrong key is reported, not silently ignored.
    page.evaluate("localStorage.setItem('dia-owner-key', 'wrong-key')")
    _ask(page, "bad key question")
    page.wait_for_function(
        "document.querySelector('#agentOutput').textContent.includes('Owner key not recognised')"
    )
    page.close()
