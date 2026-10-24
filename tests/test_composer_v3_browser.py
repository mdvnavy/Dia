"""Composer v3 + first-screen cleanup (fix-prompt-bar-mic-corner; Glyph spec 2026-10-23_composer-v3_spec.md; Ikenna
9:49-10:00 PM ET): mic fixed in the corner, send fading in just left of it, 48/44px bar with 36px translucent buttons,
compact drawer, no duplicate Ctrl K hint, the 3-step intro, the (?) help popover, NEW tags on a first visit, and the
cache headers / versioned assets that keep a deployed build from being masked by a stale one."""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

RECT = "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom} }"
VISIBLE = "e => { const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.5 && r.width > 0 }"
COMPOSERS = [("#intakeComposer", "#questionnaire", "#runAgent", "#intakeMic", "#intakePlusButton"),
             ("#agentComposer", "#agentMessage", "#runChat", "#micButton", "#agentPlusButton")]
WIDTHS = [1280, 390, 360]


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
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
            except Exception as error:  # noqa: BLE001 - try the next candidate
                last_error = error
        else:
            pytest.skip(f"no Chromium available for Playwright: {last_error}")
        yield b
        b.close()


@pytest.fixture
def open_page(browser, base_url):
    contexts, errors = [], []

    def _open(theme="light", width=1280, height=800, touch=False, ask_dia=False):
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                      has_touch=touch, user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
        contexts.append(context)
        p = context.new_page()
        p.on("pageerror", lambda error: errors.append(str(error)))
        p.add_init_script(f"localStorage.setItem('dia-theme', '{theme}')")
        if ask_dia:   # the browser's view of a server with ASK_DIA_ENABLED on; no model call is made
            p.route("**/api/agent/status", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"configured": True, "enabled": True})))
        p.goto(base_url + "/")
        p.wait_for_function("document.querySelector('#agentStatus').textContent !== 'checking...'")
        return p

    yield _open
    for context in contexts:
        context.close()
    assert errors == [], errors


def box(p, selector):
    return p.eval_on_selector(selector, RECT)


def overlaps(a, b):
    return a["x"] < b["x"] + b["w"] - 0.5 and b["x"] < a["x"] + a["w"] - 0.5 and \
        a["y"] < b["y"] + b["h"] - 0.5 and b["y"] < a["y"] + a["h"] - 0.5


def type_into(p, textarea, text):
    p.evaluate("([s, t]) => { const el = document.querySelector(s); el.value = t; el.dispatchEvent(new Event('input', {bubbles: true})); }", [textarea, text])
    p.wait_for_timeout(260)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("composer", COMPOSERS, ids=["intake", "ask"])
def test_mic_stays_in_corner_and_send_fades_in_left_of_it(open_page, width, theme, composer):
    bar_sel, ta, run, mic, plus = composer
    p = open_page(theme=theme, width=width, height=844 if width < 600 else 800, ask_dia=True)
    if bar_sel == "#agentComposer":
        p.evaluate("openAgent()")
        p.wait_for_timeout(350)
    bar, mic_empty, plus_box = box(p, bar_sel), box(p, mic), box(p, plus)
    pad = 6 if width >= 768 else 4
    # Empty: the mic alone, flush in the corner, mirroring + (symmetric); send invisible and not focusable.
    assert abs(bar["r"] - mic_empty["r"] - pad) <= 1.01, (bar, mic_empty)
    assert abs((mic_empty["x"] - bar["x"]) - (bar["r"] - plus_box["r"])) <= 1.01
    assert abs((plus_box["x"] - bar["x"]) - (bar["r"] - mic_empty["r"])) <= 1.01
    assert not p.eval_on_selector(run, VISIBLE)
    assert p.eval_on_selector(run, "e => e.hidden")
    # Typed: the mic doesn't move; send sits in the slot just left of it (4px gap), no overlap.
    type_into(p, ta, "Score this intake")
    mic_typed, run_box = box(p, mic), box(p, run)
    assert mic_typed == mic_empty
    assert p.eval_on_selector(run, VISIBLE)
    assert abs(mic_typed["x"] - run_box["r"] - 4) <= 1.01, (run_box, mic_typed)
    assert not overlaps(run_box, mic_typed)
    assert run_box["r"] <= bar["r"] and run_box["x"] >= bar["x"]
    # Text never runs under send: the textarea's padding reserves the slot.
    pr = p.eval_on_selector(ta, "e => { const r = e.getBoundingClientRect(); return r.right - parseFloat(getComputedStyle(e).paddingRight) }")
    assert pr <= run_box["x"] + 0.5
    # Emptied again: send goes away, mic still put.
    type_into(p, ta, "")
    assert box(p, mic) == mic_empty


def test_tab_order_plus_textarea_send_mic(open_page):
    p = open_page()
    type_into(p, "#questionnaire", "Score this intake")
    p.focus("#intakePlusButton")
    order = []
    for _ in range(3):
        p.keyboard.press("Tab")
        order.append(p.evaluate("document.activeElement.id"))
    assert order == ["questionnaire", "runAgent", "intakeMic"]


@pytest.mark.parametrize("width", WIDTHS)
def test_bar_and_button_sizes(open_page, width):
    p = open_page(width=width, height=844 if width < 600 else 800)
    bar = box(p, "#intakeComposer")
    assert round(bar["h"]) == (48 if width >= 768 else 44)
    if width >= 768:
        assert round(bar["w"]) == 640
    else:
        assert round(bar["w"]) == width - 32
    for sel in ("#intakePlusButton", "#intakeMic", "#trayButton", "#cabinetButton"):
        b = box(p, sel)
        assert (round(b["w"]), round(b["h"])) == (36, 36), sel
    if width < 768:   # 44x44 hit area through the invisible ::before
        for sel in ("#intakePlusButton", "#intakeMic", "#trayButton"):
            b = box(p, sel)
            hit = p.evaluate("([x, y, s]) => document.elementFromPoint(x, y)?.closest(s) !== null", [b["x"] - 3, b["y"] + b["h"] / 2, sel])
            assert hit, sel


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_buttons_are_translucent_and_not_mustard(open_page, theme):
    p = open_page(theme=theme)
    for sel in ("#intakePlusButton", "#intakeMic", "#trayButton"):
        style = p.eval_on_selector(sel, "e => { const s = getComputedStyle(e); return [s.backgroundColor, s.backgroundImage, s.boxShadow] }")
        alpha = re.match(r"rgba\([\d.]+, [\d.]+, [\d.]+, ([\d.]+)\)", style[0])
        assert alpha and float(alpha.group(1)) < 0.2, (sel, style)
        assert style[1] == "none" and style[2] == "none", (sel, style)
    bar_bg = p.eval_on_selector("#intakeComposer", "e => getComputedStyle(e).backgroundColor")
    assert "0.55" in bar_bg or "0.88" in bar_bg or "0.92" in bar_bg, bar_bg
    html = p.content().lower()
    assert "#dfa81c" not in p.evaluate("[...document.querySelectorAll('.prompt-bar *')].map(e => getComputedStyle(e).backgroundColor + getComputedStyle(e).borderColor).join()").lower()
    assert html


@pytest.mark.parametrize("width", [1280, 390])
def test_drawer_is_a_small_floating_pill_above_the_bar(open_page, width):
    p = open_page(width=width, height=844 if width < 600 else 800)
    p.click("#intakeVoiceNotch")
    p.wait_for_timeout(300)
    fan, bar = box(p, "#intakeVoiceFan"), box(p, "#intakeComposer")
    assert round(fan["h"]) == 44 and fan["w"] <= 130, fan
    assert abs(bar["y"] - fan["b"] - 8) <= 1.01
    assert abs((fan["x"] + fan["w"] / 2) - (bar["x"] + bar["w"] / 2)) <= 1.01
    for sel in ("#status", "#trayButton", "#cabinetButton"):
        assert not overlaps(fan, box(p, sel)), sel


@pytest.mark.parametrize("width", [1280, 390])
def test_exactly_one_ctrl_k_hint_and_it_is_the_top_one(open_page, width):
    p = open_page(width=width, height=844 if width < 600 else 800)
    hints = p.evaluate("""[...document.querySelectorAll('kbd, .kbd-hint')].filter(e => {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        return /K$/.test(e.textContent.trim()) && r.width > 0 && s.visibility !== 'hidden' && !e.closest('[hidden], dialog:not([open])');
      }).map(e => [e.id, Math.round(e.getBoundingClientRect().top)])""")
    if width >= 768:
        assert hints == [["paletteShortcutHint", hints[0][1]]] and hints[0][1] < 52
    else:   # the phone header shows the search icon only; no Ctrl K hint anywhere else
        assert len(hints) <= 1 and all(h[0] == "paletteShortcutHint" for h in hints)
    assert p.locator(".footer-note").count() == 0


def test_first_screen_is_decluttered(open_page):
    p = open_page()
    assert p.locator(".subtitle").count() == 0
    assert p.inner_text("#titleBrand").strip().startswith("DIA")
    steps = p.eval_on_selector_all("#introSteps > li", "els => els.map(e => e.textContent.trim())")
    assert steps == ["Paste the discovery questionnaire to score the opportunity.",
                     "Flag the missing details.", "Draft the profile analysis and proposal."]
    assert p.locator("#uploadLink").count() == 0
    assert p.locator(".actions.under-prompt .dot-sep").count() == 0
    assert not p.is_visible("#loadSample") and not p.is_visible("#intakeNote")
    assert "Discovery Intake Agent." not in p.inner_text(".center-stage")
    status, bar = box(p, "#status"), box(p, "#intakeComposer")
    assert abs(status["y"] - bar["b"] - 10) <= 1.01
    assert p.evaluate("document.body.innerText").lower().count("dictation") == 0


def test_tray_opens_the_file_picker_by_click_and_keyboard(open_page):
    p = open_page()
    with p.expect_file_chooser() as chooser:
        p.click("#trayButton")
    assert chooser.value.element.get_attribute("accept") == ".txt,.md,.markdown,.text,.csv,.tsv,.json,.rtf,.docx,.doc"
    p.focus("#trayButton")
    with p.expect_file_chooser():
        p.keyboard.press("Enter")


BANNED = ["Cloud Run", "revision", "env", "MEMORY_SHEET", "Vertex", "ADK", "PR #", "branch", "gcloud", "Forge", "Bash",
          "Jules", "coming soon", "only grounded", "never ungrounded", "not a chatbot", "read in your browser", "opt-in",
          "sanitiz", "analytics", "80K", "token", "Discovery Intake Agent", "Sheet"]
STEPS = ["Paste a questionnaire to score the opportunity.", "Fill in the missing details DIA flags.",
         "Review the profile, analysis, and proposal."]
FOOT_ON = "Scoring is rule-based and doesn't use AI. Only Ask DIA and its suggestions send text to Gemini."
FOOT_OFF = "Scoring is rule-based and doesn't use AI. Ask DIA is off, so no text goes to Gemini."


def open_help(p, keyboard=False):
    if keyboard:
        p.focus("#helpPopoverBtn")
        p.keyboard.press("Enter")
    else:
        p.click("#helpPopoverBtn")
    p.wait_for_timeout(250)   # 160ms opacity + translateY(4px -> 0)
    assert p.is_visible("#helpPopover")


@pytest.mark.parametrize("width", [1280, 390, 360])
def test_help_icon_placement_and_size(open_page, width):
    p = open_page(width=width, height=844 if width < 600 else 800)
    assert p.locator("#appInfo").count() == 0   # the old "?" tooltip is retired
    icon, hair = box(p, "#helpPopoverBtn"), p.evaluate("document.querySelector('.top-nav').getBoundingClientRect().bottom")
    brand = box(p, "#navBrandMenu")
    brand_pad = p.eval_on_selector("#navBrandMenu", "e => parseFloat(getComputedStyle(e).paddingLeft)")
    inset = p.evaluate("""() => { const d = document.createElement('div'); d.style.width = 'var(--lh-inset)';
        document.querySelector('.top-nav').appendChild(d); const w = d.getBoundingClientRect().width; d.remove(); return w; }""")
    assert p.get_attribute("#helpPopoverBtn", "aria-label") == "Help"
    assert p.get_attribute("#helpPopoverBtn", "aria-expanded") == "false"
    assert (round(icon["w"]), round(icon["h"])) == (28, 28)
    assert abs(icon["y"] - hair - 12) <= 1.01
    assert abs(icon["x"] - (brand["x"] + brand_pad)) <= 1.01   # flush with the wordmark/crumb text
    assert abs(icon["x"] - inset) <= 1.01                       # = --lh-inset
    hit = p.eval_on_selector("#helpPopoverBtn", "e => { const s = getComputedStyle(e, '::before'); return s.content !== 'none' && parseFloat(s.top) === -8 }")
    assert hit   # 28 + 2 x 8 = 44px hit area
    for dx, dy in ((-7, 14), (34, 14), (14, -7), (14, 34)):
        assert p.evaluate("([x, y]) => !!document.elementFromPoint(x, y)?.closest('#helpPopoverBtn')", [icon["x"] + dx, icon["y"] + dy])


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 390, 360])
def test_help_popover_layout_and_copy(open_page, width, theme):
    p = open_page(theme=theme, width=width, height=844 if width < 600 else 800)
    open_help(p, keyboard=True)
    assert p.evaluate("document.activeElement.id") == "helpPopoverTitle"
    assert p.get_attribute("#helpPopoverBtn", "aria-expanded") == "true"
    icon, pop = box(p, "#helpPopoverBtn"), box(p, "#helpPopover")
    assert abs(pop["y"] - icon["b"] - 8) <= 1.01 and abs(pop["x"] - icon["x"]) <= 1.01
    assert round(pop["w"]) == (300 if width >= 768 else width - 32)
    assert pop["r"] <= width - 15
    style = p.eval_on_selector("#helpPopover", "e => { const s = getComputedStyle(e); return [s.backgroundColor, s.backdropFilter || s.webkitBackdropFilter, s.borderRadius, s.paddingLeft] }")
    assert style[0] == ("rgba(20, 20, 22, 0.88)" if theme == "dark" else "rgba(253, 252, 250, 0.9)"), style
    assert "blur(16px)" in style[1] and style[2] == "14px" and style[3] == "16px"
    assert p.inner_text("#helpPopoverTitle") == "Getting started"
    assert p.eval_on_selector_all("#helpPopover .hp-steps > li", "els => els.map(e => e.textContent)") == STEPS
    assert p.inner_text("#loadSample") == "Load sample" and p.inner_text("#helpPopoverLearn") == "Learn how to use DIA"
    assert p.inner_text("#intakeNote").strip() == FOOT_ON
    text = p.inner_text("#helpPopover")
    for word in BANNED:
        assert word.lower() not in text.lower(), word
    assert not re.search(r"gemini[- ]?\d|flash|\bpro\b|deterministic", text, re.I)
    assert p.get_attribute("#helpPopoverClose", "aria-label") == "Close help"


def test_help_popover_dismissal_and_focus(open_page):
    p = open_page()
    open_help(p, keyboard=True)
    p.keyboard.press("Escape")
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "helpPopoverBtn"
    open_help(p)
    p.click("#helpPopoverClose")
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "helpPopoverBtn"
    open_help(p)
    p.click("#helpPopoverBtn")   # trigger click while open closes and keeps focus on the trigger
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "helpPopoverBtn"
    open_help(p)
    p.click("#questionnaire")    # outside click: closes, the clicked target keeps focus
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "questionnaire"
    open_help(p, keyboard=True)  # tabbing past the last item closes it (non-modal)
    for _ in range(6):
        p.keyboard.press("Tab")
    assert p.is_hidden("#helpPopover")
    open_help(p)
    p.set_viewport_size({"width": 500, "height": 800})   # crossing 600px closes it, focus back on the trigger
    p.wait_for_timeout(100)
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "helpPopoverBtn"


def test_help_and_whats_new_are_never_open_together(open_page):
    p = open_page()
    open_help(p)
    p.click("#whatsNew")
    p.wait_for_selector("#whatsNewPanel", state="visible")
    assert p.is_hidden("#helpPopover")
    p.click("#helpPopoverBtn")
    p.wait_for_timeout(250)
    assert p.is_visible("#helpPopover") and p.is_hidden("#whatsNewPanel")


def test_footnote_off_line_and_describedby(open_page):
    p = open_page()
    assert p.get_attribute("#questionnaire", "aria-describedby").split()[0] == "intakeNote"
    p.evaluate("document.documentElement.dataset.assistant = 'off'")
    open_help(p)
    assert p.inner_text("#intakeNote").strip() == FOOT_OFF


def test_learn_opens_help_and_settings_and_returns_focus(open_page):
    p = open_page()
    open_help(p)
    p.click("#helpPopoverLearn")
    assert p.is_hidden("#helpPopover")
    assert p.evaluate("document.querySelector('#diaHelpDialog').open")
    p.keyboard.press("Escape")
    p.wait_for_function("!document.querySelector('#diaHelpDialog').open")
    assert p.evaluate("document.activeElement.id") == "helpPopoverBtn"


def test_load_sample_empty_box_loads(open_page):
    p = open_page()
    open_help(p)
    p.click("#loadSample")
    p.wait_for_function("document.querySelector('#questionnaire').value.length > 100")
    assert p.is_hidden("#helpPopover") and p.evaluate("document.activeElement.id") == "questionnaire"
    assert "Sample loaded" in p.inner_text("#status")


def test_load_sample_filled_box_confirms(open_page):
    p = open_page()
    type_into(p, "#questionnaire", "My real client notes")
    open_help(p)
    p.click("#loadSample")
    assert p.is_visible("#helpPopoverConfirm") and p.is_hidden("#helpPopoverActions")
    assert p.inner_text("#helpPopoverConfirm .hp-confirm-text") == "Replace your text with the sample?"
    assert p.get_attribute("#helpPopoverConfirm", "role") == "group"
    assert p.evaluate("document.activeElement.id") == "helpPopoverReplace"
    p.click("#helpPopoverCancel")   # Cancel keeps the text and puts the actions back
    assert p.input_value("#questionnaire") == "My real client notes"
    assert p.is_visible("#helpPopoverActions") and p.evaluate("document.activeElement.id") == "loadSample"
    p.click("#loadSample")
    p.keyboard.press("Escape")      # Esc in the confirm = Cancel; the popover stays open
    assert p.is_visible("#helpPopover") and p.is_visible("#helpPopoverActions")
    p.click("#loadSample")
    p.click("#helpPopoverReplace")
    p.wait_for_function("document.querySelector('#questionnaire').value.length > 100")
    assert p.is_hidden("#helpPopover")
    open_help(p)                    # the sample already in the box: no confirm
    p.click("#loadSample")
    p.wait_for_timeout(150)
    assert p.is_hidden("#helpPopover")


def test_load_sample_failure_shows_the_error(open_page):
    p = open_page()
    p.route("**/api/sample", lambda route: route.fulfill(status=500, body="nope"))
    open_help(p)
    p.click("#loadSample")
    p.wait_for_selector("#helpPopoverError", state="visible")
    assert p.inner_text("#helpPopoverError") == "Couldn't load the sample. Try again."
    assert p.get_attribute("#helpPopoverError", "role") == "alert"
    assert p.is_visible("#helpPopover") and p.is_enabled("#loadSample")
    assert p.input_value("#questionnaire") == ""


def test_other_load_sample_entry_points_confirm_through_a_toast(open_page):
    p = open_page()
    type_into(p, "#questionnaire", "My real client notes")
    p.click("#navBrandMenu")
    p.click("#navDropdown [data-command='load-sample']")
    p.wait_for_selector("#toast", state="visible")
    assert p.inner_text("#toastText") == "Replace your text with the sample?"
    assert p.input_value("#questionnaire") == "My real client notes"
    p.click("#toastActions .toast-action")
    p.wait_for_function("document.querySelector('#questionnaire').value.length > 100")
    p.route("**/api/sample", lambda route: route.abort())
    type_into(p, "#questionnaire", "")
    p.click("#navBrandMenu")
    p.click("#navDropdown [data-command='load-sample']")
    p.wait_for_function("document.querySelector('#toastText').textContent === \"Couldn't load the sample. Try again.\"")


def test_help_popover_in_forced_colors(browser, base_url):
    context = browser.new_context(viewport={"width": 1280, "height": 800}, forced_colors="active")
    p = context.new_page()
    p.goto(base_url + "/")
    p.click("#helpPopoverBtn")
    p.wait_for_timeout(250)
    shadow, border = p.eval_on_selector("#helpPopover", "e => [getComputedStyle(e).boxShadow, getComputedStyle(e).borderTopStyle]")
    assert shadow == "none" and border == "solid"
    assert p.eval_on_selector("#helpPopoverBtn", "e => getComputedStyle(e).borderTopStyle") == "solid"
    context.close()


def test_palette_has_getting_started(open_page):
    p = open_page()
    p.keyboard.press("Control+k")
    p.fill("#paletteInput", "getting started")
    p.keyboard.press("Enter")
    p.wait_for_timeout(250)
    assert p.is_visible("#helpPopover")


def test_new_tag_shows_on_first_visit(open_page):
    p = open_page()
    p.wait_for_function("document.querySelector('#whatsNew').dataset.state === 'new'")
    assert p.evaluate("localStorage.getItem('dia-whats-new-seen')") is None


def test_live_notes_are_approved_real_notes():
    notes = json.load(open(os.path.join(os.path.dirname(__file__), "..", "static", "whats-new.json"), encoding="utf-8"))["notes"]
    assert notes and all(n["live_flag"] is True and n["approved_by"] for n in notes)
    assert all(n["title"] != n["body"] and n["title"].lower() != "earlier" for n in notes)


def _get(base_url, path):
    with urllib.request.urlopen(base_url + path) as response:
        return response.headers, response.read().decode("utf-8", "replace")


def test_cache_headers_and_versioned_assets(base_url):
    headers, html = _get(base_url, "/")
    assert headers["Cache-Control"] == "no-cache"
    refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert refs and all(re.search(r"\?v=[0-9a-f]{12}$", ref) for ref in refs), refs
    build = re.search(r'<meta name="dia-build" content="([0-9a-f]{12})">', html).group(1)
    v_headers, _ = _get(base_url, "/api/version")
    assert v_headers["Cache-Control"] == "no-store"
    assert json.loads(_get(base_url, "/api/version")[1])["build"] == build
    css = next(ref for ref in refs if ref.startswith("/static/composer.css"))
    assert _get(base_url, css)[0]["Cache-Control"] == "public, max-age=31536000, immutable"
    assert _get(base_url, "/static/composer.css")[0]["Cache-Control"] == "no-cache"
    assert _get(base_url, "/static/whats-new.json?v=1")[0]["Cache-Control"] == "no-cache"
    assert _get(base_url, "/api/config")[0].get("Cache-Control") is None   # /api/config untouched
