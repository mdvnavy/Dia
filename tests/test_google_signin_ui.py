"""Lockdown v2 UI: sign-in gate and the Glyph usage counter (static + Playwright)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import app
from lockdown_support import CLIENT_ID, Server, gate_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
GIS_SRC = "https://accounts.google.com/gsi/client"

# Stand-in for Google Identity Services: renders a plain button that hands the
# page a fixed credential (window.__TEST_CRED) the fake server verifier knows.
FAKE_GIS = """
window.__gisCalls = [];
window.google = { accounts: { id: {
  initialize(config) { window.__gisConfig = config; window.__gisCalls.push('initialize'); },
  renderButton(el, options) {
    window.__gisCalls.push('renderButton:' + (el.id || ''));
    const b = document.createElement('button');
    b.className = 'gisButton'; b.type = 'button'; b.textContent = 'Sign in with Google';
    b.onclick = () => window.__gisConfig.callback({ credential: window.__TEST_CRED || 'visitor-cred' });
    el.append(b);
  },
  disableAutoSelect() { window.__gisCalls.push('disableAutoSelect'); },
} } };
"""


# --- static checks (always run) ------------------------------------------


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_sign_in_script_is_served_and_loaded():
    html = read("templates/index.html")
    assert '<script src="/static/google-signin.js"></script>' in html
    assert html.index("/static/app.js") < html.index("/static/google-signin.js")
    assert app.PUBLIC_FILES["/static/google-signin.js"].exists()
    assert not (ROOT / "static" / "owner-key.js").exists()


def test_counter_markup_matches_glyph_spec():
    html = read("templates/index.html")
    meter = re.search(r'<span id="usageMeter"[^>]*>', html).group(0)
    for attr in ('role="status"', 'aria-describedby="usageReset"', "hidden"):
        assert attr in meter
    # Immediately left of SEARCH ACTIONS, with the divider between.
    assert html.index('id="usageMeter"') < html.index('id="usageDivider"') < html.index("lh-search-label")
    assert 'id="usageInline"' in html and 'id="menuUsage"' in html
    assert ">Owner · unlimited<" in html


def test_counter_css_uses_only_existing_tokens():
    css = read("static/style.css")
    block = re.sub(r"/\*.*?\*/", "", css[css.index("*/", css.index("Glyph lockdown v2")) + 2:], flags=re.S)
    assert "#DFA81C".lower() not in block.lower() and "mustard" not in block.lower()
    assert not re.search(r"#[0-9a-f]{3,6}\b", block, re.I), "no raw colours in the counter block"
    assert "tabular-nums" in block and "0.06em" in block and "11px" in block
    assert re.search(r"\.lh-usage-divider\s*{[^}]*width:\s*1px[^}]*height:\s*12px", block, re.S)
    assert "var(--muted)" in block and "var(--text)" in block
    assert not re.search(r"red|#f00|danger|warning", block, re.I)


def test_client_never_stores_or_sends_counts():
    for rel in ("static/app.js", "static/google-signin.js"):
        js = read(rel)
        assert "localStorage.setItem(\"dia_usage" not in js
        assert not re.search(r"(local|session)Storage[^\n]*(usage|quota|remaining)", js, re.I), rel
    signin = read("static/google-signin.js")
    assert "console." not in signin and "localStorage" not in signin
    assert "disableAutoSelect" in signin and "/api/auth/logout" in signin
    assert "quotaBadge" not in signin


def test_limit_copy_has_no_price_or_urgency():
    js = read("static/app.js")
    assert "More at midnight ET." in js
    block = js[js.index("lockdown v2"):js.index("window.DIA_USAGE")]
    assert not re.search(r"\$\d", block)  # no prices
    for word in ("upgrade", "Pro ", "hurry", "countdown", "price"):
        assert word not in block


# --- browser checks (skipped without Playwright/Chromium) -----------------


@pytest.fixture(scope="module")
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
def live(gate_env):
    srv = Server()
    yield srv
    srv.close()


def open_page(browser, url, width=1280, height=900, theme=None):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.route(GIS_SRC + "*", lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_GIS))
    if theme:
        page.add_init_script(f"try {{ localStorage.setItem('dia-theme', '{theme}'); }} catch (e) {{}}")
    page.goto(url)
    page.wait_for_function("document.body.dataset.auth !== 'pending'")
    return page


def sign_in(page, cred="visitor-cred"):
    page.evaluate(f"window.__TEST_CRED = '{cred}'")
    page.wait_for_selector("#signInGateButton .gisButton", state="visible")
    page.click("#signInGateButton .gisButton")
    page.wait_for_function("document.body.dataset.auth === 'signed-in'")
    page.wait_for_function("!document.querySelector('#usageMeter').hidden")


def ask(page, text="a question"):
    page.evaluate(
        """(text) => { document.body.dataset.agent = 'open';
                       document.querySelector('#agentMessage').value = text;
                       document.querySelector('#runChat').click(); }""",
        text,
    )


def test_signed_out_sees_calm_gate_no_composer_no_counter(browser, live):
    page = open_page(browser, live.url)
    assert page.evaluate("document.body.dataset.auth") == "signed-out"
    assert page.is_visible("#signInGate")
    assert page.text_content("#signInGateText") == "Sign in with Google to run an intake and ask DIA."
    assert page.is_visible("#signInGateButton .gisButton")
    for sel in ("#questionnaire", "#runAgent", "#agentComposer", "#loadSample", "#uploadLink", "#usageMeter"):
        assert not page.is_visible(sel), sel
    # Read-only content (the landing page) is still there.
    assert page.is_visible("header")
    page.context.close()


def test_sign_in_not_configured_says_so_calmly(browser, live, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    page = open_page(browser, live.url)
    assert page.evaluate("document.body.dataset.auth") == "signed-out"
    assert "isn't available right now" in page.text_content("#signInGateText")
    page.context.close()


def test_sign_in_shows_counter_and_run_updates_it(browser, live):
    page = open_page(browser, live.url)
    sign_in(page)
    assert page.is_visible("#questionnaire") and page.is_editable("#questionnaire")
    meter = page.locator("#usageMeter")
    assert meter.inner_text().replace("\n", " ").upper().split() == "GENERATIONS 10/10 · ASK DIA 5/5".split()
    assert meter.get_attribute("aria-live") == "off"  # silent on first fill
    assert page.get_attribute("#usageMeter", "title") == (
        "10 generations and 5 Ask DIA questions left today. Resets at midnight ET."
    )
    assert page.text_content("#usageReset") == page.get_attribute("#usageMeter", "title")
    # Counter sits immediately left of SEARCH ACTIONS, divider between.
    assert page.evaluate(
        "document.querySelector('#usageMeter').nextElementSibling.nextElementSibling.id === 'usageDivider'"
        " || document.querySelector('#usageMeter').nextElementSibling.id === 'usageDivider'"
    )
    page.fill("#questionnaire", "Company: Example Co\nProblem: intake takes too long")
    page.click("#runAgent")
    page.wait_for_function("document.querySelector('[data-bucket=generations] .lh-usage-num').textContent === '9/10'")
    assert meter.get_attribute("aria-live") == "polite"
    assert page.evaluate("document.cookie").find("dia_session") == -1  # HttpOnly
    page.context.close()


def render(page, g_left, a_left):
    page.evaluate(
        """([g, a]) => window.DIA_USAGE.render({signed_in: true, owner: false, unlimited: false,
              generations: {limit: 10, used: 10 - g, remaining: g},
              ask: {limit: 5, used: 5 - a, remaining: a},
              resets_at: '2026-10-24T00:00:00-04:00', timezone: 'America/Toronto'})""",
        [g_left, a_left],
    )


def test_low_and_zero_levels_render(browser, live):
    page = open_page(browser, live.url)
    sign_in(page)
    render(page, 1, 3)
    assert page.get_attribute("[data-bucket=generations]", "data-level") == "low"
    assert page.get_attribute("[data-bucket=ask]", "data-level") == "ok"
    weight = page.evaluate("getComputedStyle(document.querySelector('[data-bucket=generations] .lh-usage-num')).fontWeight")
    assert weight == "600"
    dot = page.evaluate("""(() => { const s = getComputedStyle(document.querySelector('[data-bucket=generations] .lh-usage-dot'));
        return [s.width, s.height, s.backgroundColor, s.borderTopStyle, s.display]; })()""")
    assert dot[:2] == ["6px", "6px"] and dot[3] == "solid" and dot[4] != "none"
    assert dot[2] in ("rgba(0, 0, 0, 0)", "transparent")  # hollow ring
    assert page.get_attribute("#usageMeter", "title").startswith("1 generation and 3 Ask DIA questions left today.")
    render(page, 0, 3)
    assert page.get_attribute("[data-bucket=generations]", "data-level") == "zero"
    filled = page.evaluate("getComputedStyle(document.querySelector('[data-bucket=generations] .lh-usage-dot')).backgroundColor")
    text = page.evaluate("getComputedStyle(document.querySelector('[data-bucket=generations] .lh-usage-num')).color")
    assert filled == text  # filled dot in --text
    assert page.text_content("[data-bucket=generations] .lh-usage-num") == "0/10"
    assert page.get_attribute("#runAgent", "aria-disabled") == "true"
    assert page.get_attribute("#runAgent", "title") == "You've used today's 10 generations. More at midnight ET."
    page.fill("#questionnaire", "Company: Example")  # Run shows once there is text
    assert page.is_visible("#runAgent") and page.is_editable("#questionnaire")
    # ok level: no dot.
    render(page, 7, 3)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-bucket=ask] .lh-usage-dot')).display") == "none"
    assert page.get_attribute("#runAgent", "aria-disabled") is None
    page.context.close()


def test_ask_429_flow_is_calm(browser, live, monkeypatch):
    monkeypatch.setenv("DIA_ASK_DAILY_LIMIT", "1")
    page = open_page(browser, live.url)
    sign_in(page)
    ask(page)
    page.wait_for_function("document.querySelector('#agentOutput').textContent === 'fake agent reply'")
    assert page.get_attribute("#runChat", "aria-disabled") == "true"  # 0 left
    # Clicking the aria-disabled Run just re-shows the message; nothing is sent.
    ask(page)
    message = "You've used today's 1 Ask DIA question. More at midnight ET."
    # Landing polish (#40): the message is #status's detail slot; on a limit the state word and dot step aside.
    page.wait_for_function(f"document.querySelector('#statusText').textContent === {message!r}")
    assert page.inner_text("#status").strip() == message
    assert page.evaluate("document.querySelector('#status').classList.contains('is-limit')")
    page.fill("#agentMessage", "another")
    assert page.is_visible("#runChat") and page.is_editable("#agentMessage")
    assert page.get_attribute("#runChat", "aria-disabled") == "true"
    # The status line goes to --text, no red.
    color = page.evaluate("getComputedStyle(document.querySelector('#status')).color")
    assert color in {
        page.evaluate("getComputedStyle(document.querySelector('[data-bucket=ask] .lh-usage-num')).color"),
    }
    page.context.close()


def test_server_429_even_if_client_thinks_it_has_quota(browser, live, monkeypatch):
    monkeypatch.setenv("DIA_GENERATION_DAILY_LIMIT", "1")
    page = open_page(browser, live.url)
    sign_in(page)
    render(page, 9, 5)  # a stale/forged client view
    page.fill("#questionnaire", "Company: Example\nProblem: slow")
    page.click("#runAgent")
    page.wait_for_function("document.querySelector('[data-bucket=generations] .lh-usage-num').textContent === '0/1'")
    page.evaluate("""(() => { const run = document.querySelector('#runAgent');
        run.removeAttribute('aria-disabled'); run.click(); })()""")  # forged client state
    page.wait_for_function("document.querySelector('#status').textContent.includes(\"You've used today's 1 generation.\")")
    assert page.get_attribute("#runAgent", "aria-disabled") == "true"
    page.context.close()


def test_owner_renders_unlimited_without_numbers(browser, live):
    page = open_page(browser, live.url)
    sign_in(page, "owner-cred")
    page.wait_for_function("!document.querySelector('.lh-usage-owner').hidden")
    assert page.inner_text("#usageMeter").strip().upper() == "OWNER · UNLIMITED"
    assert page.get_attribute("#usageMeter", "title") is None
    assert page.get_attribute("#usageMeter", "aria-describedby") is None
    assert not page.is_visible("[data-bucket=generations]")
    page.context.close()


def test_mobile_counter_joins_status_line_and_menu(browser, live):
    page = open_page(browser, live.url, width=390, height=844)
    sign_in(page)
    render(page, 7, 3)
    assert not page.is_visible("#usageMeter")
    assert page.is_visible("#usageInline")
    # Landing polish (#40) moved #status above the bar; the counter copy sits right after it.
    line = (page.inner_text("#status") + " " + page.inner_text("#usageInline")).replace("\n", " ")
    assert re.sub(r"\s+", " ", line).strip() == "Ready · 7/10 generations · 3/5 Ask DIA"
    assert page.evaluate("!document.querySelector('#menuUsage').hidden")
    page.context.close()


def test_sign_out_hides_counter_and_restores_gate(browser, live):
    page = open_page(browser, live.url)
    sign_in(page)
    page.click("#diaSignOut")
    page.wait_for_function("document.body.dataset.auth === 'signed-out'")
    assert not page.is_visible("#usageMeter") and page.is_visible("#signInGate")
    assert any(c == "disableAutoSelect" for c in page.evaluate("window.__gisCalls"))
    page.context.close()


def test_kill_switch_degrades_ask_dia_calmly(browser, live, monkeypatch):
    monkeypatch.setenv("ASK_DIA_ENABLED", "false")
    page = open_page(browser, live.url)
    sign_in(page)
    page.wait_for_function("document.querySelector('#agentStatus').textContent === 'temporarily unavailable'")  # main's #41 wording
    assert page.evaluate("document.querySelector('#runChat').disabled") is True
    page.context.close()


def test_desktop_counter_layout_at_1280(browser, live):
    page = open_page(browser, live.url, width=1280, height=800)
    sign_in(page)
    box = page.evaluate("""(() => { const r = s => document.querySelector(s).getBoundingClientRect();
        return {clock: r('#lhClock').right, meter: [r('#usageMeter').left, r('#usageMeter').right, r('#usageMeter').top + r('#usageMeter').height / 2],
                div: [r('#usageDivider').left, r('#usageDivider').right, r('#usageDivider').height],
                search: [r('.lh-search-label').left, r('.lh-search-label').top + r('.lh-search-label').height / 2]}; })()""")
    assert box["meter"][0] > box["clock"], "counter must not overlap the centred clock"
    assert round(box["div"][0] - box["meter"][1]) == 16
    assert round(box["search"][0] - box["div"][1]) == 16
    assert box["div"][2] == 12
    assert abs(box["meter"][2] - box["search"][1]) <= 1  # same line
    font = page.evaluate("""(() => { const s = getComputedStyle(document.querySelector('#usageMeter'));
        return [s.fontSize, s.letterSpacing, s.textTransform]; })()""")
    assert font[0] == "11px" and font[2] == "uppercase"
    assert font[1] in ("0.66px", "0.06em")
    page.context.close()


def test_between_breakpoints_counter_uses_status_line(browser, live):
    page = open_page(browser, live.url, width=1024, height=800)
    sign_in(page)
    assert not page.is_visible("#usageMeter") and page.is_visible("#usageInline")
    page.context.close()


def _assert_locked(page):
    assert page.evaluate("document.body.dataset.auth") == "signed-out"
    assert page.is_visible("#signInGate") and page.is_visible("#signInGateButton .gisButton")
    for sel in ("#questionnaire", "#usageMeter"):
        assert not page.is_visible(sel), sel


def _assert_unlocked(page):
    assert page.evaluate("document.body.dataset.auth") == "signed-in"
    assert not page.is_visible("#signInGate")
    assert page.is_visible("#questionnaire") and page.is_visible("#usageMeter")


def test_full_flow_unlock_reload_lock(browser, live):
    page = open_page(browser, live.url)
    sent = []
    page.on("request", lambda r: sent.append((r.url, r.headers.get("authorization"))) if "/api/auth/session" in r.url else None)
    _assert_locked(page)
    sign_in(page)
    _assert_unlocked(page)
    # Tia's wiring: the credential travels as "Authorization: Bearer", once.
    assert sent and sent[-1][1] == "Bearer visitor-cred"
    stored = page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage), document.cookie])")
    assert "visitor-cred" not in stored and "dia_session" not in stored  # HttpOnly, not in storage
    page.reload()
    page.wait_for_function("document.body.dataset.auth !== 'pending'")
    page.wait_for_function("!document.querySelector('#usageMeter').hidden")
    _assert_unlocked(page)  # the session cookie survived the reload
    page.click("#diaSignOut")
    page.wait_for_function("document.body.dataset.auth === 'signed-out'")
    _assert_locked(page)
    page.reload()
    page.wait_for_function("document.body.dataset.auth !== 'pending'")
    _assert_locked(page)  # and sign-out survives it too
    page.context.close()


def test_browser_sign_in_with_real_signed_token(browser, live, monkeypatch):
    """The GIS stub hands over a token signed by a local RSA key; google-auth verifies it for real."""
    import agent_auth
    from test_sign_in_flow import StubGoogleCerts, mint

    monkeypatch.setattr(agent_auth, "google_verifier", agent_auth._google_verify)
    monkeypatch.setattr(agent_auth, "google_request_adapter", StubGoogleCerts("x509"))
    page = open_page(browser, live.url)
    sign_in(page, mint(sub="4000001", email="browser@example.com"))
    _assert_unlocked(page)
    page.context.close()
    page = open_page(browser, live.url)
    page.evaluate("window.__TEST_CRED = 'forged.' + 'a.b'")
    page.click("#signInGateButton .gisButton")
    page.wait_for_function("document.querySelector('#signInGateText').textContent.includes('failed') || document.querySelector('#status').textContent.includes('failed')", timeout=5000)
    _assert_locked(page)
    page.context.close()
