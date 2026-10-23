"""Landing polish (feat/landing-polish), in a real browser: mic in the bar's corner, the floating voice notch
(open/close, keyboard, actions, clean close), status above the bar with the note below, and Ready/Processing/Complete.

Skipped automatically when Playwright or a Chromium build is not installed (same fixtures as test_ui_browser.py).
"""

import os
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

WIDTHS = [(1280, 800), (768, 900), (390, 844)]


@pytest.fixture(scope="module")
def base_url():
    os.environ.pop("MEMORY_SHEET_ID", None)
    os.environ.pop("OBS_CAPTURE", None)
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

    def _open(theme="light", width=1280, height=800, ask_dia=False):
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
        contexts.append(context)
        p = context.new_page()
        p.on("pageerror", lambda error: errors.append(str(error)))
        p.add_init_script(f"localStorage.setItem('dia-theme', '{theme}')")
        if ask_dia:   # the browser's view of a server with ASK_DIA_ENABLED on (#41's kill switch is off by default)
            p.route("**/api/agent/status", lambda route: route.fulfill(json={"configured": True, "enabled": True}))
        p.goto(base_url + "/")
        p.wait_for_function("document.querySelector('#agentStatus').textContent !== 'checking...'")
        return p

    yield _open
    for context in contexts:
        context.close()
    assert errors == [], errors


def box(p, selector):
    return p.eval_on_selector(selector, "e => { const r = e.getBoundingClientRect(); "
                                        "return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom}; }")


def inside(inner, outer):
    return (inner["x"] >= outer["x"] and inner["y"] >= outer["y"]
            and inner["r"] <= outer["r"] and inner["b"] <= outer["b"])


# ---------- 1. mic in the prompt bar's corner ----------

@pytest.mark.parametrize("width,height", WIDTHS)
def test_mic_sits_inside_the_bar_corner(open_page, width, height):
    p = open_page(width=width, height=height)
    bar, mic, plus = box(p, "#intakeComposer"), box(p, "#intakeMic"), box(p, "#intakePlusButton")
    assert inside(mic, bar)
    # Empty bar: the mic mirrors the plus, tucked into the right-hand corner (same inset, same vertical centre).
    left_inset, right_inset = plus["x"] - bar["x"], bar["r"] - mic["r"]
    assert abs(left_inset - right_inset) <= 2, (left_inset, right_inset)
    assert abs((mic["y"] + mic["h"] / 2) - (plus["y"] + plus["h"] / 2)) <= 1
    assert mic["w"] >= 44 and mic["h"] >= 44  # prompt bar v2: real 44px targets everywhere
    # With text, the mic stays exactly where it was and Run appears just to its LEFT, inside the bar.
    # Measured relative to the bar (the bar itself lifts 2px on focus/hover).
    rel = lambda: {k: round(v - (bar_now["x"] if k in ("x", "r") else bar_now["y"] if k in ("y", "b") else 0), 2)
                   for k, v in box(p, "#intakeMic").items()}
    bar_now = box(p, "#intakeComposer")
    before = rel()
    # Prompt bar v2: the empty bar centres its placeholder (no right padding); the Run slot is reserved from the first
    # typed character, which is also when Run appears, so typed text never reflows.
    p.fill("#questionnaire", "C")
    padding = p.eval_on_selector("#questionnaire", "e => getComputedStyle(e).paddingRight")
    p.fill("#questionnaire", "Client: Gearhead")
    p.wait_for_selector("#runAgent:not([hidden])")
    p.wait_for_timeout(300)
    bar_now = box(p, "#intakeComposer")
    assert rel() == before
    bar, mic, run = box(p, "#intakeComposer"), box(p, "#intakeMic"), box(p, "#runAgent")
    assert inside(mic, bar) and inside(run, bar) and run["r"] <= mic["x"] and mic["x"] - run["r"] <= 10
    assert abs((run["y"] + run["h"] / 2) - (mic["y"] + mic["h"] / 2)) <= 1
    # Run's slot is reserved in the textarea's right padding, so typing never reflows the text.
    assert p.eval_on_selector("#questionnaire", "e => getComputedStyle(e).paddingRight") == padding
    assert float(padding.rstrip("px")) >= run["w"]


BUTTON_LOOK = """s => { const e = document.querySelector(s), cs = getComputedStyle(e);
  return {bg: cs.backgroundImage, color: cs.color, border: cs.borderStyle, opacity: +cs.opacity, visibility: cs.visibility,
          disabled: e.disabled, ariaDisabled: e.getAttribute('aria-disabled')}; }"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_run_with_text_is_a_full_enabled_button_silver_in_light_graphite_in_dark(open_page, theme):
    """Glyph (PR #40): dark Run is graphite (#3a3a40 -> #26262a, like Tia), never gold; light stays the mic's silver."""
    p = open_page(theme=theme)
    p.click("#loadSample")
    p.wait_for_selector("#runAgent:not([hidden])")
    p.wait_for_timeout(300)
    run, mic = p.evaluate(BUTTON_LOOK, "#runAgent"), p.evaluate(BUTTON_LOOK, "#intakeMic")
    assert run["disabled"] is False and run["ariaDisabled"] is None
    assert run["opacity"] == 1 and run["visibility"] == "visible" and run["border"] == "solid"
    if theme == "light":
        assert run["bg"] == mic["bg"] and run["color"] == mic["color"]  # unchanged: same silver fill and ink as the mic
        assert "rgb(230, 232, 235)" in run["bg"] and "rgb(184, 189, 196)" in run["bg"]
    else:
        assert "rgb(58, 58, 64)" in run["bg"] and "rgb(38, 38, 42)" in run["bg"], run
        assert _luminance(_rgb(run["color"])) > 0.7   # light icon ink on graphite
    # Never gold/mustard (dark --accent #c8a96e, drag-over #DFA81C), resting or hovered.
    for state in ("rest", "hover"):
        if state == "hover":
            p.hover("#runAgent")
            p.wait_for_timeout(300)
        styles = p.eval_on_selector("#runAgent", "e => { const c = getComputedStyle(e); return [c.backgroundImage, c.color, c.borderColor, c.boxShadow].join(' '); }")
        assert "200, 169, 110" not in styles and "223, 168, 28" not in styles, (state, styles)
    run_box, mic_box = box(p, "#runAgent"), box(p, "#intakeMic")
    assert run_box["r"] <= mic_box["x"]


@pytest.mark.parametrize("width,height", WIDTHS)
def test_composer_order_is_plus_text_run_mic_and_the_mic_mirrors_the_plus(open_page, width, height):
    """Glyph: order [+] text [Run] [mic]; the rule is that the mic's inset mirrors the plus's
    (12px inside the border on desktop and tablet; 4px on phones, where prompt bar v2's slimmer bar pads 4px)."""
    p = open_page(width=width, height=height)
    p.fill("#questionnaire", "Client: Gearhead")
    p.wait_for_selector("#runAgent:not([hidden])")
    p.wait_for_timeout(300)
    bar, plus, text, run, mic = (box(p, s) for s in ("#intakeComposer", "#intakePlusButton", "#questionnaire", "#runAgent", "#intakeMic"))
    text_end = text["r"] - float(p.eval_on_selector("#questionnaire", "e => getComputedStyle(e).paddingRight").rstrip("px"))
    assert plus["r"] <= text["x"] + 1 and text_end <= run["x"] + 1 and run["r"] <= mic["x"], (plus, text, run, mic)
    border = float(p.eval_on_selector("#intakeComposer", "e => getComputedStyle(e).borderRightWidth").rstrip("px"))
    plus_inset, mic_inset = plus["x"] - bar["x"] - border, (bar["r"] - border) - mic["r"]
    assert abs(mic_inset - plus_inset) <= 0.5, ("mic must mirror the plus", plus_inset, mic_inset)
    assert abs(mic_inset - (12 if width > 600 else 4)) <= 0.5, mic_inset


BAR_LOOK = """() => { const c = getComputedStyle(document.querySelector('#intakeComposer'));
  return {outline: c.outlineColor + ' ' + c.outlineStyle, shadow: c.boxShadow}; }"""
GOLD = ("200, 169, 110", "223, 168, 28")


@pytest.mark.parametrize("state", ["focus", "hover"])
def test_dark_bar_glow_is_neutral_silver_never_gold(open_page, state):
    """Glyph: in dark the bar's glow is #7C828A at 0.22 with the same 0 0 40px blur/spread as the old gold one."""
    p = open_page(theme="dark")
    p.mouse.move(2, 2)
    p.focus("#questionnaire") if state == "focus" else p.hover("#intakeComposer", position={"x": 300, "y": 10})
    p.wait_for_timeout(400)
    look = p.evaluate(BAR_LOOK)
    assert "rgba(124, 130, 138, 0.22) 0px 0px 40px 0px" in look["shadow"], look
    assert not any(g in look["outline"] + look["shadow"] for g in GOLD), look
    if state == "focus":
        assert look["outline"] == "rgb(124, 130, 138) solid"


def test_light_bar_focus_is_unchanged(open_page):
    p = open_page(theme="light")
    p.mouse.move(2, 2)
    p.focus("#questionnaire")
    p.wait_for_timeout(400)
    assert p.evaluate(BAR_LOOK) == {"outline": "rgb(95, 102, 112) solid",
        "shadow": "rgba(28, 20, 8, 0.22) 0px 30px 72px 0px, rgba(163, 169, 177, 0.22) 0px 0px 40px 0px, "
                  "rgba(255, 255, 255, 0.9) 0px 2px 3px 0px inset, rgba(255, 255, 255, 0.22) 0px -2px 4px 0px inset"}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_drag_over_keeps_its_mustard(open_page, theme):
    p = open_page(theme=theme)
    p.evaluate("document.body.classList.add('file-dragging')")
    p.wait_for_timeout(300)
    assert p.eval_on_selector(".tray-icon", "e => getComputedStyle(e).borderColor") == "rgb(223, 168, 28)"
    assert p.eval_on_selector("#intakeComposer", "e => getComputedStyle(e).outlineStyle") == "dashed"


def _sample_mic_and_run(p, action):
    """Run `action` and sample the mic box and Run's opacity every animation frame for ~400ms."""
    return p.evaluate("""async (action) => {
      const ta = document.querySelector('#questionnaire'), mic = document.querySelector('#intakeMic'), run = document.querySelector('#runAgent');
      const read = () => { const r = mic.getBoundingClientRect(); return {mic: [r.x, r.y, r.width, r.height],
        op: +getComputedStyle(run).opacity, scale: getComputedStyle(run).scale}; };
      const frames = [read()];
      ta.value = action === 'in' ? 'Client: Gearhead' : '';
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      const start = performance.now();
      while (performance.now() - start < 400) { await new Promise(requestAnimationFrame); frames.push(read()); }
      return frames;
    }""", action)


@pytest.mark.parametrize("direction", ["in", "out"])
def test_run_fades_both_ways_and_the_mic_never_moves(open_page, direction):
    p = open_page()
    p.mouse.move(2, 2)
    if direction == "out":
        p.fill("#questionnaire", "Client: Gearhead")
        p.wait_for_timeout(300)
    frames = _sample_mic_and_run(p, direction)
    assert len({tuple(f["mic"]) for f in frames}) == 1, "the mic moved during the Run transition"
    opacities = [f["op"] for f in frames]
    # Animated, not instant: at least one in-between frame, and it ends fully shown / fully hidden.
    assert any(0 < o < 1 for o in opacities), opacities
    assert opacities[-1] == (1 if direction == "in" else 0)
    assert p.get_attribute("#runAgent", "hidden") is (None if direction == "in" else "")
    if direction == "out":
        assert p.is_hidden("#runAgent")


def test_composer_icons_are_smaller_in_both_themes(open_page):
    sizes = {}
    for theme in ("light", "dark"):
        p = open_page(theme=theme)
        sizes[theme] = p.eval_on_selector_all(
            "#intakeComposer .cmp-circle .icon svg",
            "els => els.filter(e => e.getClientRects().length).map(e => Math.round(e.getBoundingClientRect().width))")
        button = box(p, "#intakeMic")
        assert button["w"] == 44  # prompt bar v2: 44px circles (was 46)
    assert sizes["light"] == sizes["dark"] and sizes["light"] and all(16 <= s <= 18 for s in sizes["light"])


# ---------- 2. floating notch ----------

FAN_STATE = """() => {
  const fan = document.querySelector('#intakeVoiceFan'), cs = getComputedStyle(fan);
  const tip = getComputedStyle(document.querySelector('#intakeNotchTip'));
  return {inert: fan.inert, opacity: +cs.opacity, visibility: cs.visibility, pointer: cs.pointerEvents,
          shadow: cs.boxShadow, filter: cs.filter, backdrop: cs.backdropFilter || 'none',
          tipOpacity: +tip.opacity, tipVisibility: tip.visibility,
          expanded: document.querySelector('#intakeVoiceNotch').getAttribute('aria-expanded')};
}"""


def test_floating_notch_sits_just_above_the_bar(open_page):
    p = open_page()
    notch, bar, handle = box(p, "#intakeVoiceNotch"), box(p, "#intakeComposer"), box(p, "#intakeVoiceNotch .notch-handle")
    assert notch["b"] < bar["y"] and bar["y"] - notch["b"] <= 12  # floats just above, not on the edge
    assert abs((notch["x"] + notch["w"] / 2) - (bar["x"] + bar["w"] / 2)) <= 1
    assert handle["h"] <= 6 and handle["w"] <= 40  # a small handle bar
    assert p.get_attribute("#intakeVoiceNotch", "aria-haspopup") == "menu"
    assert p.eval_on_selector("#intakeVoiceNotch", "e => e.tagName") == "BUTTON"


def test_floating_notch_opens_and_closes_with_mouse_and_has_the_actions(open_page):
    p = open_page()
    p.click("#intakeVoiceNotch")
    assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "true"
    p.wait_for_function("getComputedStyle(document.querySelector('#intakeVoiceFan')).opacity === '1'")
    labels = p.eval_on_selector_all("#intakeVoiceFan .voice-item", "els => els.map(e => e.getAttribute('aria-label'))")
    assert [label.split(":")[0].split(" (")[0] for label in labels] == ["Conversation mode", "Listen", "Auto-speak replies"]
    fan, bar = box(p, "#intakeVoiceFan"), box(p, "#intakeComposer")
    assert fan["b"] < bar["y"]  # the panel floats above the bar
    # Actions keep proxying the Ask DIA controls: Auto-speak toggles #autoSpeak and leaves the fan open.
    if p.get_attribute("#intakeFanAutoSpeak", "aria-disabled") != "true":
        p.click("#intakeFanAutoSpeak")
        assert p.get_attribute("#autoSpeak", "aria-pressed") == "true"
        assert p.get_attribute("#intakeFanAutoSpeak", "aria-checked") == "true"
        assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "true"
    p.click("#intakeVoiceNotch")
    assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "false"


def test_floating_notch_keyboard_and_focus_return(open_page):
    p = open_page()
    p.focus("#intakeVoiceNotch")
    p.keyboard.press("Enter")
    assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "true"
    assert p.evaluate("document.activeElement.id") == "intakeFanListen"
    p.keyboard.press("ArrowRight")
    assert p.evaluate("document.activeElement.id") == "intakeFanAutoSpeak"
    p.keyboard.press("Escape")
    assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "false"
    assert p.evaluate("document.activeElement.id") == "intakeVoiceNotch"
    p.keyboard.press("ArrowDown")
    assert p.get_attribute("#intakeVoiceNotch", "aria-expanded") == "true"
    p.keyboard.press("Escape")
    assert p.evaluate("document.activeElement.id") == "intakeVoiceNotch"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("how", ["mouse", "keyboard"])
def test_floating_notch_closes_cleanly(open_page, theme, how):
    """No faint shadow after closing: hidden + inert, opacity 0, no pointer events, no shadow or filter,
    and the 'Voice options' tooltip doesn't fade back into the fan's spot while the handle is still hovered/focused."""
    p = open_page(theme=theme)
    if how == "mouse":
        p.click("#intakeVoiceNotch")
        p.wait_for_timeout(400)
        p.click("#intakeVoiceNotch")  # pointer stays on the handle
    else:
        p.focus("#intakeVoiceNotch")
        p.keyboard.press("Enter")
        p.wait_for_timeout(400)
        p.keyboard.press("Escape")  # focus returns to the handle
    p.wait_for_timeout(600)  # longer than every close transition (120ms) and the tooltip intent delay (120ms)
    state = p.evaluate(FAN_STATE)
    assert state["expanded"] == "false"
    assert state["inert"] is True and state["visibility"] == "hidden"
    assert state["opacity"] == 0 and state["pointer"] == "none"
    assert state["shadow"] == "none" and state["filter"] == "none" and state["backdrop"] == "none"
    assert state["tipOpacity"] == 0 and state["tipVisibility"] == "hidden"
    # Nothing in the fan's spot answers a hit test.
    fan = box(p, "#intakeVoiceFan")
    hit = p.evaluate(f"document.elementFromPoint({fan['x'] + fan['w'] / 2}, {fan['y'] + fan['h'] / 2})?.closest('#intakeVoiceFan')")
    assert hit is None
    # The tooltip is only muted right after a close: leaving the handle (or blurring it) unmutes it.
    if how == "mouse":
        p.mouse.move(5, 5)
    else:
        p.focus("#questionnaire")
    assert p.get_attribute("#intakeVoiceNotch", "data-tip-muted") is None


LAYOUT = """() => {
  const rect = (e) => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, r: r.right, b: r.bottom, w: r.width, h: r.height}; };
  const sub = document.querySelector('.subtitle'), range = document.createRange(); range.selectNodeContents(sub);
  const lines = [...range.getClientRects()];
  const fan = document.querySelector('#intakeVoiceFan'), cs = getComputedStyle(fan);
  return {fan: rect(fan), status: rect(document.querySelector('#status')), notch: rect(document.querySelector('#intakeVoiceNotch')),
          lastLine: rect({getBoundingClientRect: () => lines[lines.length - 1]}),
          statusOpacity: +getComputedStyle(document.querySelector('#status')).opacity,
          background: cs.backgroundColor, shadow: cs.boxShadow,
          raised: getComputedStyle(document.documentElement).getPropertyValue('--raised').trim(),
          menuShadow: getComputedStyle(document.querySelector('#intakePlusMenu')).boxShadow};
}"""


def _overlaps(a, b):
    return a["x"] < b["r"] and b["x"] < a["r"] and a["y"] < b["b"] and b["y"] < a["b"]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_open_voice_panel_leaves_status_and_description_uncovered(open_page, theme, width, height):
    """Ikenna: with the panel open, 'Ready' and the hero's last line ('proposal.') stay fully visible."""
    p = open_page(theme=theme, width=width, height=height)
    p.click("#intakeVoiceNotch")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakeVoiceFan')).opacity === '1'")
    p.wait_for_timeout(400)  # status slide + item rise settled
    layout = p.evaluate(LAYOUT)
    assert not _overlaps(layout["fan"], layout["status"]), layout
    assert not _overlaps(layout["fan"], layout["lastLine"]), layout
    assert not _overlaps(layout["status"], layout["lastLine"]), layout
    assert layout["statusOpacity"] == 1 and p.is_visible("#statusState")
    # Bottom edge at least 10px above the handle; panel inside the viewport.
    assert layout["notch"]["y"] - layout["fan"]["b"] >= 10, layout
    assert layout["fan"]["x"] >= 0 and layout["fan"]["r"] <= width
    # An opaque --raised layer with the plus menu's shadow.
    assert _rgb(layout["background"]) == _hex(layout["raised"]) and "rgba" not in layout["background"]
    assert layout["shadow"] != "none" and layout["shadow"] == layout["menuShadow"]
    # Compact: a single row of the three actions.
    tops = p.eval_on_selector_all("#intakeVoiceFan .voice-item", "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(tops) == 3 and len(set(tops)) == 1
    # Closing puts the status line back.
    p.click("#intakeVoiceNotch")
    p.wait_for_timeout(400)
    closed = p.evaluate(LAYOUT)
    assert closed["statusOpacity"] == 1
    p.mouse.move(2, 2)


RECT = "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, r: r.right, b: r.bottom, w: r.width, h: r.height}; }"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_open_voice_panel_clears_the_tray_and_cabinet_buttons_at_390(open_page, theme):
    """Bash/Ikenna: at 390 the open panel stays between the dock buttons, inside the bar, off the status line."""
    p = open_page(theme=theme, width=390, height=844)
    p.click("#intakeVoiceNotch")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakeVoiceFan')).opacity === '1'")
    p.wait_for_timeout(400)
    fan, bar = p.eval_on_selector("#intakeVoiceFan", RECT), p.eval_on_selector("#intakeComposer", RECT)
    for dock in ("#trayButton", "#cabinetButton"):
        assert p.is_visible(dock)
        assert not _overlaps(fan, p.eval_on_selector(dock, RECT)), (dock, fan)
    assert fan["x"] >= bar["x"] and fan["r"] <= bar["r"], (fan, bar)
    layout = p.evaluate(LAYOUT)
    assert not _overlaps(layout["fan"], layout["status"]) and not _overlaps(layout["fan"], layout["lastLine"]), layout
    # Icon-only, but every action still has its accessible name; targets stay 36px.
    items = p.eval_on_selector_all("#intakeVoiceFan .voice-item", "els => els.map(e => [e.getAttribute('aria-label'), Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height)])")
    assert len(items) == 3 and all(label and w >= 36 and h >= 36 for label, w, h in items), items
    p.click("#intakeVoiceNotch")


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_open_ask_dia_voice_panel_leaves_the_heading_uncovered(open_page, theme, width, height):
    """Bash/Ikenna: the Ask DIA panel never covers that composer's heading (the heading slides up, like the status)."""
    p = open_page(theme=theme, width=width, height=height, ask_dia=True)
    p.evaluate("() => document.querySelector('.dropdown-item[data-command=\"ask\"]')?.click() || (document.body.dataset.agent = 'open')")
    p.wait_for_selector("#agentVoiceNotch", state="visible")
    p.eval_on_selector("#agentVoiceNotch", "e => e.scrollIntoView({block: 'center'})")
    gap = "() => document.querySelector('#agentComposer').getBoundingClientRect().top - document.querySelector('#agentStage > h2').getBoundingClientRect().top"
    closed_gap = p.evaluate(gap)   # measured against the bar: the page may scroll while the notch takes focus
    p.click("#agentVoiceNotch")
    p.wait_for_function("getComputedStyle(document.querySelector('#agentVoiceFan')).opacity === '1'")
    p.wait_for_timeout(400)
    fan, notch = p.eval_on_selector("#agentVoiceFan", RECT), p.eval_on_selector("#agentVoiceNotch", RECT)
    h2, badge = p.eval_on_selector("#agentStage > h2", RECT), p.eval_on_selector("#agentStatus", RECT)
    above = p.eval_on_selector(".intake-stage > .prompt-callout", RECT)
    assert not _overlaps(fan, h2) and not _overlaps(fan, badge), (fan, h2)
    assert h2["y"] >= above["b"] + 12, (h2, above)              # the slide stays in the stage's top margin, with air
    assert notch["y"] - fan["b"] >= 10 and fan["x"] >= 0 and fan["r"] <= width, (fan, notch)
    assert p.eval_on_selector("#agentStage > h2", "e => +getComputedStyle(e).opacity") == 1
    p.keyboard.press("Escape")
    p.mouse.move(2, 2)
    assert p.get_attribute("#agentVoiceNotch", "aria-expanded") == "false"
    p.wait_for_timeout(400)
    p.wait_for_function("getComputedStyle(document.querySelector('#agentStage > h2')).translate === 'none'")
    assert abs(p.evaluate(gap) - closed_gap) <= 2   # back in place (the bar keeps its 2px focus lift)


def _hex(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


# ---------- reduced motion ----------

@pytest.fixture
def open_reduced(browser, base_url):
    contexts = []

    def _open(theme="light", width=1280, height=800):
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme, reduced_motion="reduce")
        contexts.append(context)
        p = context.new_page()
        p.add_init_script(f"localStorage.setItem('dia-theme', '{theme}')")
        p.goto(base_url + "/")
        p.wait_for_function("document.querySelector('#agentStatus').textContent !== 'checking...'")
        return p

    yield _open
    for context in contexts:
        context.close()


def test_reduced_motion_processing_dot_is_static_at_full_opacity(open_reduced):
    p = open_reduced()
    p.evaluate("setRunState('processing')")
    dot = p.eval_on_selector("#status .status-dot", "e => { const cs = getComputedStyle(e); return {anim: cs.animationName, opacity: +cs.opacity, transform: cs.transform}; }")
    assert dot == {"anim": "none", "opacity": 1, "transform": "none"}


def test_reduced_motion_voice_panel_opens_with_a_fade_only_and_closes_in_120ms(open_reduced):
    p = open_reduced()
    p.click("#intakeVoiceNotch")
    opened = p.evaluate("""() => { const fan = document.querySelector('#intakeVoiceFan'), item = fan.querySelector('.voice-item');
      const f = getComputedStyle(fan), i = getComputedStyle(item);
      return {fanProps: f.transitionProperty, itemProps: i.transitionProperty, itemTransform: i.transform, fanTranslate: f.transform,
              delay: i.transitionDelay, status: getComputedStyle(document.querySelector('#status')).transitionDuration}; }""")
    assert "transform" not in opened["fanProps"] and "transform" not in opened["itemProps"], opened
    assert opened["itemTransform"] == "none" and opened["fanTranslate"] == "none"
    assert set(opened["delay"].replace(" ", "").split(",")) == {"0s"}
    assert opened["status"] == "0s"  # the status line moves out of the way without animating
    p.click("#intakeVoiceNotch")
    closing = p.eval_on_selector("#intakeVoiceFan", "e => { const cs = getComputedStyle(e); return [cs.transitionProperty, cs.transitionDuration, cs.transitionTimingFunction]; }")
    assert closing[0].startswith("opacity") and closing[1].startswith("0.12s") and closing[2].startswith("linear"), closing


def test_reduced_motion_run_is_a_short_fade_without_scale(open_reduced):
    p = open_reduced()
    p.fill("#questionnaire", "Client: Gearhead")
    run = p.eval_on_selector("#runAgent", "e => { const cs = getComputedStyle(e); return [cs.scale, cs.transitionProperty, cs.transitionDuration, cs.animationName]; }")
    assert run[0] in ("none", "1") and run[3] == "none"
    assert run[1].startswith("opacity") and "scale" not in run[1] and float(run[2].split(",")[0].rstrip("s")) <= 0.1, run
    p.fill("#questionnaire", "")
    hidden = p.eval_on_selector("#runAgent", "e => getComputedStyle(e).scale")
    assert hidden in ("none", "1")


# ---------- 3. line order ----------

@pytest.mark.parametrize("width,height", WIDTHS)
def test_status_above_the_bar_and_note_below(open_page, width, height):
    p = open_page(width=width, height=height)
    status, note, bar = box(p, "#status"), box(p, "#intakeNote"), box(p, "#intakeComposer")
    assert status["b"] <= bar["y"]
    assert note["y"] >= bar["b"]
    assert p.inner_text("#intakeNote") == "Scoring is deterministic. Only Ask DIA and its suggestions send text to Gemini."
    under = box(p, ".intake-stage .under-prompt")
    assert note["y"] >= under["b"]  # where Ready used to be, under the links


# ---------- 4. status states ----------

def _luminance(rgb):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _rgb(css):
    return tuple(int(float(v)) for v in re.findall(r"[\d.]+", css)[:3])


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_ready_is_green_with_enough_contrast(open_page, theme):
    p = open_page(theme=theme)
    assert p.get_attribute("#status", "aria-live") == "polite" and p.get_attribute("#status", "role") == "status"
    assert p.get_attribute("#status", "data-state") == "ready"
    assert p.text_content("#statusState") == "Ready"
    assert p.is_visible("#status .status-dot")
    colour = _rgb(p.eval_on_selector("#statusState", "e => getComputedStyle(e).color"))
    background = _rgb(p.evaluate("getComputedStyle(document.body).backgroundColor"))
    assert colour[1] > colour[0] and colour[1] > colour[2]  # green
    lighter, darker = sorted([_luminance(colour), _luminance(background)], reverse=True)
    assert (lighter + 0.05) / (darker + 0.05) >= 4.5


def test_status_ready_processing_complete(open_page):
    p = open_page()
    p.click("#loadSample")
    p.wait_for_function("document.querySelector('#questionnaire').value.length > 0")
    assert p.get_attribute("#status", "data-state") == "ready"
    held = []
    p.route("**/api/process", lambda route: held.append(route))
    p.click("#runAgent")
    p.wait_for_function("document.querySelector('#status').dataset.state === 'processing'")
    assert p.text_content("#statusState") == "Processing"
    for _ in range(250):
        if held:
            break
        p.wait_for_timeout(20)
    assert held, "the /api/process request never started"
    held[0].continue_()
    p.wait_for_function("document.querySelector('#status').dataset.state === 'complete'")
    assert p.text_content("#statusState") == "Complete"
    assert p.text_content("#status").startswith("Complete")
    # Editing the intake afterwards returns to a green Ready.
    p.click("#intakeToggle") if p.is_visible("#intakeToggle") and p.is_hidden("#questionnaire") else None
    p.fill("#questionnaire", "Client: Gearhead")
    p.wait_for_function("document.querySelector('#status').dataset.state === 'ready'")
    assert p.text_content("#statusState") == "Ready"


# ---------- narrow placeholder and plus-menu viewport guard ----------

def test_kill_switch_placeholder_survives_a_resize(open_page):
    """ASK_DIA_ENABLED off (#41): the narrow/wide placeholder swap must not bring back "Ask DIA a question…"."""
    p = open_page(width=390, height=844)
    p.evaluate("openAgent()")
    assert p.get_attribute("#agentMessage", "placeholder") == "Ask DIA is temporarily unavailable."
    p.set_viewport_size({"width": 1280, "height": 800})
    p.wait_for_timeout(100)
    assert p.get_attribute("#agentMessage", "placeholder") == "Ask DIA is temporarily unavailable."
    p.set_viewport_size({"width": 390, "height": 844})
    p.wait_for_timeout(100)
    assert p.get_attribute("#agentMessage", "placeholder") == "Ask DIA is temporarily unavailable."


@pytest.mark.parametrize("selector", ["#questionnaire", "#agentMessage"])
def test_placeholder_is_not_clipped_at_390(open_page, selector):
    p = open_page(width=390, height=844, ask_dia=True)
    if selector == "#agentMessage":
        p.evaluate("openAgent()")
        p.wait_for_selector("#agentMessage", state="visible")
    fits = p.eval_on_selector(selector, """el => {
      const cs = getComputedStyle(el), c = document.createElement('canvas').getContext('2d');
      c.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return {text: el.placeholder, need: c.measureText(el.placeholder).width, room};
    }""")
    assert fits["text"] == p.get_attribute(selector, "data-placeholder-short")
    assert fits["need"] <= fits["room"], fits
    # Back to the full copy on a wide screen.
    p.set_viewport_size({"width": 1280, "height": 800})
    p.wait_for_function(f"document.querySelector('{selector}').placeholder !== {fits['text']!r}")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_plus_menu_stays_inside_the_viewport_at_1280x720(open_page, theme):
    p = open_page(theme=theme, width=1280, height=720)
    p.click("#intakePlusButton")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakePlusMenu')).opacity === '1'")
    p.wait_for_timeout(300)  # let the open transform settle
    menu = box(p, "#intakePlusMenu")
    header = box(p, "header.lh")
    # Never over the 52px letterhead band: top clamped 8px below it.
    assert menu["y"] >= 60 and menu["y"] >= header["b"] + 8 - 0.5, (menu, header)
    assert menu["x"] >= 0 and menu["b"] <= 720 and menu["r"] <= 1280, menu
    # All commands is a footer row that's always visible, inside the menu.
    footer = box(p, "#intakePlusMenu .pm-foot .pm-item")
    assert p.text_content("#intakePlusMenu .pm-foot .pm-label") == "All commands"
    assert inside(footer, menu) and p.is_visible("#intakePlusMenu .pm-foot .pm-item")
    hit = p.evaluate(f"document.elementFromPoint({footer['x'] + footer['w'] / 2}, {footer['y'] + footer['h'] / 2})?.closest('.pm-foot') !== null")
    assert hit
    # The middle scrolls, with the bottom-edge fade cue until it reaches the end.
    cue = """e => { const a = getComputedStyle(e, '::after'), r = e.getBoundingClientRect();
      return {overflow: getComputedStyle(e).overflowY, scrolls: e.scrollHeight > e.clientHeight + 1, more: e.dataset.more || null,
              opacity: +a.opacity, image: a.backgroundImage, height: a.height, position: a.position}; }"""
    scroll = p.eval_on_selector("#intakePlusMenu .pm-scroll", cue)
    assert scroll["overflow"] == "auto" and scroll["scrolls"], scroll
    # Tia's scroll fade (bfae6fd): a sticky 28px gradient at the bottom edge while there's more to scroll.
    assert scroll["more"] == "true" and "gradient" in scroll["image"] and scroll["position"] == "sticky", scroll
    assert scroll["height"] == "28px"
    p.wait_for_function("+getComputedStyle(document.querySelector('#intakePlusMenu .pm-scroll'), '::after').opacity === 1")
    p.eval_on_selector("#intakePlusMenu .pm-scroll", "e => { e.scrollTop = e.scrollHeight; }")
    p.wait_for_function("!document.querySelector('#intakePlusMenu .pm-scroll').dataset.more")
    p.wait_for_function("+getComputedStyle(document.querySelector('#intakePlusMenu .pm-scroll'), '::after').opacity === 0")
    # Every row is reachable, and desktop rows stay 36px.
    heights = p.eval_on_selector_all("#intakePlusMenu .pm-row > .pm-item", "els => els.map(e => e.getBoundingClientRect().height)")
    assert heights and all(h == 36 for h in heights), heights
    p.keyboard.press("Escape")


def test_plus_menu_has_no_scroll_fade_when_everything_fits(open_page):
    p = open_page(width=1280, height=1100)
    p.click("#intakePlusButton")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakePlusMenu')).opacity === '1'")
    p.wait_for_timeout(400)   # rows finish their 4px rise
    state = p.eval_on_selector("#intakePlusMenu .pm-scroll", """e => [e.scrollHeight <= e.clientHeight + 2, e.dataset.more || null,
      +getComputedStyle(e, '::after').opacity]""")
    assert state == [True, None, 0]
    p.keyboard.press("Escape")


def test_plus_menu_keyboard_reaches_every_row_when_capped(open_page):
    p = open_page(width=1280, height=720)
    p.focus("#intakePlusButton")
    p.keyboard.press("ArrowUp")  # opens on the first item
    labels = p.eval_on_selector_all("#intakePlusMenu .pm-item", "els => els.length")
    seen = set()
    for _ in range(labels + 1):
        seen.add(p.evaluate("document.activeElement.textContent"))
        p.keyboard.press("ArrowDown")
    assert any("All commands" in text for text in seen)
    p.keyboard.press("Escape")
