"""Prompt bar v2 (feat/prompt-bar-v2, Ikenna 2026-10-23): a slimmer bar with centred messages, no pills, icon-only
audio toggles in the voice options drawer with hints, and no overlap on phones (390 and 360 wide).

Runs the real app in-process and drives it with Playwright (Chromium); skipped when Chromium isn't available."""
from __future__ import annotations

import json
import os
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

RECT = "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height} }"
INTAKE_ICONS = ["#trayButton", "#cabinetButton", "#intakePlusButton", "#intakeMic", "#intakeVoiceNotch"]
FAN_ITEMS = ["FanConversation", "FanListen", "FanAutoSpeak"]

# A visible element in the prompt area that carries its own text inside a fully rounded, wider-than-tall box with a
# border or a fill reads as a pill. The bar itself (the input field) is the one allowed capsule.
PILLS_JS = """
(root) => {
  const out = [];
  const scope = document.querySelectorAll(root);
  scope.forEach((area) => area.querySelectorAll('*').forEach((el) => {
    if (el.matches('.prompt-bar, .prompt-bar *:is(textarea), .plus-menu, .plus-menu *')) return;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || cs.visibility !== 'visible' || +cs.opacity === 0) return;
    if (el.closest('[hidden]') || el.closest('[inert]')) return;
    const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!ownText && !el.matches('.chip-button')) return;
    const radius = parseFloat(cs.borderTopLeftRadius) || 0;
    const framed = parseFloat(cs.borderTopWidth) > 0 || (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent');
    if (framed && radius >= r.height / 2 - 1 && r.width > r.height * 1.25) out.push(`${el.tagName.toLowerCase()}#${el.id}.${el.className}: ${el.textContent.trim().slice(0, 40)}`);
  }));
  return out;
}
"""


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


def hint(p):
    return p.evaluate("(() => { const t = document.querySelector('#uiHint'); return t && !t.hidden ? t.textContent : null })()")


# ---------- slimmer bar, centred message ----------

@pytest.mark.parametrize("width,max_height", [(1280, 48), (390, 44), (360, 44)])
def test_bar_is_slimmer_and_icons_keep_44px_targets(open_page, width, max_height):
    # Composer v3 (Glyph 2026-10-23): 48px bar (44 on phones), 36px buttons; phones keep a 44px hit area via ::before.
    p = open_page(width=width, height=844)
    bar = box(p, "#intakeComposer")
    assert bar["h"] <= max_height, bar   # was 70px desktop, 54px phones
    p.fill("#questionnaire", "x")
    p.wait_for_timeout(300)   # Run's fade-in
    for selector in ["#trayButton", "#cabinetButton", "#intakePlusButton", "#intakeMic", "#runAgent"]:
        b = box(p, selector)
        assert b["w"] >= 36 and b["h"] >= 36, (selector, b)
        if width < 768:
            hit = p.eval_on_selector(selector, "e => { const s = getComputedStyle(e, '::before'); return s.content !== 'none' ? e.getBoundingClientRect().width - 2 * parseFloat(s.left) : 0 }")
            assert hit >= 44, (selector, hit)


def test_message_in_an_empty_bar_is_centred(open_page):
    p = open_page(ask_dia=False)
    p.evaluate("openAgent()")
    for selector in ["#questionnaire", "#agentMessage"]:
        assert p.eval_on_selector(selector, "e => getComputedStyle(e).textAlign") == "center", selector
    assert p.get_attribute("#agentMessage", "placeholder") == "Ask DIA is temporarily unavailable."
    # The textarea sits evenly between the plus and the mic, so the centred text is centred on the bar.
    bar, area = box(p, "#intakeComposer"), box(p, "#questionnaire")
    assert abs((area["x"] + area["w"] / 2) - (bar["x"] + bar["w"] / 2)) <= 2
    # Typed text goes back to the start edge.
    p.fill("#questionnaire", "## Company\nAcme")
    assert p.eval_on_selector("#questionnaire", "e => getComputedStyle(e).textAlign") in ("start", "left")


# ---------- no pills ----------

@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 390, 360])
@pytest.mark.parametrize("ask_dia", [False, True])
def test_no_pill_styling_in_the_prompt_area(open_page, theme, width, ask_dia):
    p = open_page(theme=theme, width=width, height=844, ask_dia=ask_dia)
    p.evaluate("openAgent()")
    p.wait_for_timeout(350)
    assert p.evaluate(PILLS_JS, ".prompt-wrapper") == []
    assert p.locator(".prompt-wrapper .chip-button:visible").count() == 0
    status = p.eval_on_selector("#agentStatus", "e => { const s = getComputedStyle(e); return [s.borderTopWidth, s.backgroundColor] }")
    assert status == ["0px", "rgba(0, 0, 0, 0)"]
    # Drawer open: still nothing pill-shaped.
    p.click("#intakeVoiceNotch")
    p.wait_for_timeout(350)
    assert p.evaluate(PILLS_JS, ".prompt-wrapper") == []
    fan = box(p, "#intakeVoiceFan")
    radius = p.eval_on_selector("#intakeVoiceFan", "e => parseFloat(getComputedStyle(e).borderTopLeftRadius)")
    # Composer v3 (Glyph): the drawer is a small icon-only floating pill, 44 tall, radius 22; no text pills inside.
    assert round(fan["h"]) == 44 and radius == 22, (radius, fan)


def test_ask_dia_off_state_is_plain_text(open_page):
    p = open_page()
    assert p.text_content("#agentStatus") == "temporarily unavailable"
    assert p.get_attribute("#agentStatus", "class") == "agent-state"
    p.evaluate("openAgent()")
    assert p.is_disabled("#agentMessage")
    assert p.text_content("#agentOutput") == "Ask DIA is temporarily unavailable."


# ---------- icons: accessible name + hint on hover and keyboard focus ----------

@pytest.mark.parametrize("selector", ["#trayButton", "#cabinetButton", "#intakePlusButton"])
def test_icon_has_accessible_name_and_hover_hint(open_page, selector):
    p = open_page()
    name = p.get_attribute(selector, "aria-label")
    assert name and name.strip()
    assert p.get_attribute(selector, "title") is None   # no native title tooltip on top of the hint
    p.hover(selector)
    p.wait_for_function("!document.querySelector('#uiHint').hidden")
    expected = p.get_attribute(selector, "data-hint") or name
    assert hint(p) == expected
    assert p.get_attribute("#uiHint", "role") == "tooltip"
    tip, target = box(p, "#uiHint"), box(p, selector)
    assert not overlaps(tip, target)
    p.mouse.move(5, 5)
    p.wait_for_function("document.querySelector('#uiHint').hidden")


def test_mic_and_notch_keep_their_own_tooltips(open_page):
    p = open_page()
    for selector, tip in [("#intakeMic", "#intakeMicTip"), ("#intakeVoiceNotch", "#intakeNotchTip")]:
        assert p.get_attribute(selector, "aria-label")
        assert p.get_attribute(tip, "role") == "tooltip"
        p.hover(selector)
        p.wait_for_function(f"getComputedStyle(document.querySelector('{tip}')).visibility === 'visible'")
        assert hint(p) is None   # one tooltip at a time
        p.mouse.move(5, 5)


def test_hint_shows_on_keyboard_focus(open_page):
    p = open_page()
    p.focus("#questionnaire")
    p.evaluate("document.activeElement.blur()")
    for _ in range(40):
        p.keyboard.press("Tab")
        if p.evaluate("document.activeElement && document.activeElement.id") == "trayButton":
            break
    assert p.evaluate("document.activeElement.id") == "trayButton"
    assert hint(p) == "Drop a file here or click to upload"
    p.keyboard.press("Escape")
    assert hint(p) is None


@pytest.mark.parametrize("prefix", ["intake", "agent"])
def test_drawer_items_are_icons_with_names_and_hints(open_page, prefix):
    p = open_page(ask_dia=True)
    if prefix == "agent":
        p.evaluate("openAgent()")
        p.wait_for_timeout(350)
    p.focus(f"#{prefix}VoiceNotch")
    p.keyboard.press("ArrowDown")   # opens on Listen
    p.wait_for_timeout(350)
    for item in FAN_ITEMS:
        selector = f"#{prefix}{item}"
        name = p.get_attribute(selector, "aria-label")
        assert name and name.strip(), selector
        assert p.locator(f"{selector} .voice-label").is_hidden()   # icon only
        b = box(p, selector)
        assert b["w"] >= 36 and b["h"] >= 36, (selector, b)   # v3: 36px icons in the 44px drawer
    assert hint(p) == "Listen: read the latest reply aloud"
    p.keyboard.press("ArrowRight")
    assert hint(p) in ("Auto-speak replies · off", "Auto-speak replies (not supported) · off")
    p.hover(f"#{prefix}FanConversation")
    p.wait_for_timeout(250)
    assert hint(p).startswith("Conversation mode")


def test_long_press_on_touch_shows_the_hint_without_pressing(open_page):
    p = open_page(width=390, height=844, touch=True)
    p.evaluate("""() => {
      const el = document.querySelector('#cabinetButton');
      const r = el.getBoundingClientRect();
      const o = {bubbles: true, cancelable: true, pointerType: 'touch', isPrimary: true, clientX: r.x + 10, clientY: r.y + 10};
      el.dispatchEvent(new PointerEvent('pointerdown', o));
      window.__lp = () => { el.dispatchEvent(new PointerEvent('pointerup', o)); el.click(); };
    }""")
    p.wait_for_timeout(600)
    assert hint(p) == "Filing cabinet: this session's intakes"
    p.evaluate("window.__lp()")
    assert p.get_attribute("#cabinetButton", "aria-expanded") == "false"   # the long-press didn't open it


# ---------- audio toggles live in the drawer ----------

def test_audio_toggles_live_in_the_drawer_not_under_the_bar(open_page):
    p = open_page(ask_dia=True)
    p.evaluate("openAgent()")
    p.wait_for_timeout(350)
    for selector in ["#conversationMode", "#listenAgent", "#autoSpeak"]:
        assert p.locator(selector).count() == 1   # state holders the drawer items proxy
        assert p.is_hidden(selector), selector
    assert p.locator(".voice-sources").get_attribute("hidden") is not None
    for prefix in ["intake", "agent"]:
        fan = p.locator(f"#{prefix}VoiceFan")
        assert fan.get_attribute("role") == "menu"
        assert [fan.locator(f"#{prefix}{item}").count() for item in FAN_ITEMS] == [1, 1, 1]
    # No visible "Conversation" / "Listen" / "Auto-speak" text anywhere in the Ask DIA stage outside the drawer.
    visible = p.evaluate("""() => [...document.querySelectorAll('#agentStage *')].filter((el) => !el.closest('.voice-fan')
        && el.offsetParent !== null && [...el.childNodes].some((n) => n.nodeType === 3 && /Conversation|Auto-speak|^\\s*Listen\\s*$/.test(n.textContent)))
        .map((el) => el.id || el.className)""")
    assert visible == []
    # A drawer item still drives its source: Auto-speak flips #autoSpeak and the item's checked state.
    if p.get_attribute("#agentFanAutoSpeak", "aria-disabled") == "true":
        pytest.skip("no speechSynthesis in this Chromium")
    p.click("#agentVoiceNotch")
    p.wait_for_timeout(350)
    p.click("#agentFanAutoSpeak")
    assert p.get_attribute("#autoSpeak", "aria-pressed") == "true"
    assert p.get_attribute("#agentFanAutoSpeak", "aria-checked") == "true"
    assert p.get_attribute("#intakeFanAutoSpeak", "aria-checked") == "true"


# ---------- phones: nothing overlaps or crowds ----------

@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_overlap_on_phones(open_page, width, theme):
    p = open_page(theme=theme, width=width, height=844, touch=True, ask_dia=True)
    status, tray, cabinet, notch, bar = (box(p, s) for s in ["#status", "#trayButton", "#cabinetButton", "#intakeVoiceNotch", "#intakeComposer"])
    plus, area, mic = box(p, "#intakePlusButton"), box(p, "#questionnaire"), box(p, "#intakeMic")
    for b in [status, tray, cabinet, notch, bar, plus, area, mic]:
        assert b["x"] >= 0 and b["x"] + b["w"] <= width, b
    for a, b in [(status, tray), (status, cabinet), (tray, cabinet), (tray, notch), (cabinet, notch),
                 (tray, bar), (cabinet, bar), (notch, bar), (status, bar), (plus, area), (area, mic), (plus, mic)]:
        assert not overlaps(a, b), (a, b)
    for inner in [plus, area, mic]:
        assert inner["x"] >= bar["x"] and inner["x"] + inner["w"] <= bar["x"] + bar["w"]
    assert area["w"] >= 120   # the message still has room
    # The placeholder fits on one line without clipping mid-word.
    assert p.eval_on_selector("#questionnaire", "e => e.scrollWidth <= e.clientWidth + 1")
    # Drawer open: inside the viewport, clear of the tray, the cabinet and the status line.
    p.tap("#intakeVoiceNotch")
    p.wait_for_timeout(400)
    fan = box(p, "#intakeVoiceFan")
    assert fan["x"] >= 0 and fan["x"] + fan["w"] <= width
    status_now = box(p, "#status")
    for other in [tray, cabinet, status_now, bar]:
        assert not overlaps(fan, other), (fan, other)
    items = [box(p, f"#intake{item}") for item in FAN_ITEMS]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            assert not overlaps(items[i], items[j])
    # Composer v3: the open drawer pill sits over the (hidden) notch, so it closes with Esc or an outside tap.
    p.keyboard.press("Escape")
    p.wait_for_function("document.querySelector('#intakeVoiceNotch').getAttribute('aria-expanded') !== 'true'")
    # Ask DIA stage: heading, state text and bar clear of each other and inside the viewport.
    p.evaluate("openAgent()")
    p.wait_for_timeout(350)
    h2, state, abar = box(p, "#agentStage > h2"), box(p, "#agentStatus"), box(p, "#agentComposer")
    for b in [h2, state, abar]:
        assert b["x"] >= 0 and b["x"] + b["w"] <= width, b
    assert not overlaps(h2, abar) and not overlaps(state, abar)
    p.click("#agentVoiceNotch")
    p.wait_for_timeout(400)
    afan, h2_now = box(p, "#agentVoiceFan"), box(p, "#agentStage > h2")
    assert not overlaps(afan, h2_now) and not overlaps(afan, abar)
