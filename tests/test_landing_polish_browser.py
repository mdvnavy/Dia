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

    def _open(theme="light", width=1280, height=800):
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
        contexts.append(context)
        p = context.new_page()
        p.on("pageerror", lambda error: errors.append(str(error)))
        p.add_init_script(f"localStorage.setItem('dia-theme', '{theme}')")
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
    assert mic["w"] >= 38 and mic["h"] >= 38  # hit target unchanged (46 desktop, 38 phones)
    # With text, Run appears in the corner and both controls stay inside the bar.
    p.fill("#questionnaire", "Client: Gearhead")
    p.wait_for_selector("#runAgent:not([hidden])")
    bar, mic, run = box(p, "#intakeComposer"), box(p, "#intakeMic"), box(p, "#runAgent")
    assert inside(mic, bar) and inside(run, bar) and mic["r"] <= run["x"]


def test_composer_icons_are_smaller_in_both_themes(open_page):
    sizes = {}
    for theme in ("light", "dark"):
        p = open_page(theme=theme)
        sizes[theme] = p.eval_on_selector_all(
            "#intakeComposer .cmp-circle .icon svg",
            "els => els.filter(e => e.getClientRects().length).map(e => Math.round(e.getBoundingClientRect().width))")
        button = box(p, "#intakeMic")
        assert button["w"] == 46  # button size unchanged
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


# ---------- 3. line order ----------

@pytest.mark.parametrize("width,height", WIDTHS)
def test_status_above_the_bar_and_note_below(open_page, width, height):
    p = open_page(width=width, height=height)
    status, note, bar = box(p, "#status"), box(p, "#intakeNote"), box(p, "#intakeComposer")
    assert status["b"] <= bar["y"]
    assert note["y"] >= bar["b"]
    assert p.text_content("#intakeNote") == ("Intake scoring is deterministic and needs no API key. "
                                             "Only Ask DIA sends your text to Gemini.")
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

@pytest.mark.parametrize("selector", ["#questionnaire", "#agentMessage"])
def test_placeholder_is_not_clipped_at_390(open_page, selector):
    p = open_page(width=390, height=844)
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
    p.wait_for_timeout(250)  # let the open transform settle
    menu = box(p, "#intakePlusMenu")
    assert menu["y"] >= 0 and menu["x"] >= 0 and menu["b"] <= 720 and menu["r"] <= 1280, menu
    # Every row is reachable: either it all fits, or the capped menu scrolls.
    assert p.eval_on_selector("#intakePlusMenu", "m => m.scrollHeight <= m.clientHeight + 1 || getComputedStyle(m).overflowY === 'auto'")
    p.keyboard.press("Escape")
