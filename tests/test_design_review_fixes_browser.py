"""Glyph's second-look review of #38-#40 (glyph/2026-10-23_pr38-40_design-review.md), items 1-6, in a real browser.

1. The 390 status line with a long detail wraps as one centred paragraph, not a narrow column; the intake toggle
   gets its own line on phones.
2. The open voice panel never covers "Collapse intake" (1280, 390, 360).
3. Voice panel items are at least 44x44 on phones.
4. Tab order is textarea -> Run -> mic in both composers; Run still sits visually left of the mic.
5. Dark focus rings use --control-focus (silver), never the gold accent. Light is unchanged.
6. The inputs-card dismiss x keeps its 28px glyph box but answers taps across 44x44 on phones.

Skipped automatically when Playwright or a Chromium build is not installed (same fixtures as test_ui_browser.py).
"""

import os
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

RECT = "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, r: r.right, b: r.bottom, w: r.width, h: r.height}; }"
LONG_DETAIL = ("File read: northstar-studio-discovery-questionnaire-final.docx, 12,480 characters. "
               "Review it, then press Run. Added to your existing text.")
GOLD = "rgb(200, 169, 110)"


@pytest.fixture(scope="module")
def base_url():
    os.environ.pop("MEMORY_SHEET_ID", None)
    os.environ.pop("OBS_CAPTURE", None)
    previous = os.environ.get("ASK_DIA_ENABLED")
    os.environ["ASK_DIA_ENABLED"] = "true"  # Ask DIA composer layout is checked too (kill switch itself is untouched)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        if previous is None:
            os.environ.pop("ASK_DIA_ENABLED", None)
        else:
            os.environ["ASK_DIA_ENABLED"] = previous


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

    def _open(theme="light", width=1280, height=800, touch=False):
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                      has_touch=touch, is_mobile=touch,
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
    assert errors == []


def _box(p, selector):
    return p.eval_on_selector(selector, RECT)


def _overlaps(a, b):
    return a["x"] < b["r"] and b["x"] < a["r"] and a["y"] < b["b"] and b["y"] < a["b"]


def _show_toggle(p):
    p.evaluate("""() => { const t = document.querySelector('#intakeToggle'); t.hidden = false; t.textContent = 'Collapse intake'; }""")


def _long_status(p):
    p.evaluate("""(detail) => { const s = document.querySelector('#status'); s.dataset.state = 'complete';
      document.querySelector('#statusState').textContent = 'Complete'; document.querySelector('#statusText').textContent = detail; }""",
               LONG_DETAIL)


# ---------- 1. status line at 390 ----------

@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 360])
def test_long_status_wraps_as_a_paragraph_not_a_narrow_column(open_page, theme, width):
    p = open_page(theme=theme, width=width, height=844)
    _show_toggle(p)
    _long_status(p)
    status, detail, toggle = _box(p, "#status"), _box(p, "#statusText"), _box(p, "#intakeToggle")
    lines = p.evaluate("""() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('#statusText'));
      const rows = new Set([...r.getClientRects()].map(x => Math.round(x.top))); return rows.size; }""")
    # The detail flows across (nearly) the full width: no shrunken column.
    assert status["w"] >= width * 0.8, status
    assert lines <= (3 if width >= 390 else 4), lines
    # The state word and the detail start on the same first line (dot · Complete · detail...).
    state = _box(p, "#statusState")
    assert abs(state["y"] - detail["y"]) <= 2 or lines > 1
    # The toggle is on its own line below the status, never a squeezed third column.
    assert toggle["y"] >= status["b"] - 1, (status, toggle)
    assert p.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0


def test_short_status_stays_one_centred_row_at_390(open_page):
    p = open_page(width=390, height=844)
    status, state, dot = _box(p, "#status"), _box(p, "#statusState"), _box(p, "#status .status-dot")
    assert state["h"] <= 20 and abs((dot["y"] + dot["h"] / 2) - (state["y"] + state["h"] / 2)) <= 2
    centre = (status["x"] + status["r"]) / 2
    assert abs(centre - 195) <= 2
    assert dot["r"] <= state["x"]


def test_desktop_status_and_toggle_share_one_row(open_page):
    p = open_page(width=1280, height=800)
    _show_toggle(p)
    status, toggle = _box(p, "#status"), _box(p, "#intakeToggle")
    assert abs((status["y"] + status["h"] / 2) - (toggle["y"] + toggle["h"] / 2)) <= 3, (status, toggle)


# ---------- 2. voice panel vs Collapse intake ----------

@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844), (360, 780)])
def test_open_voice_panel_never_covers_collapse_intake(open_page, theme, width, height):
    p = open_page(theme=theme, width=width, height=height)
    _show_toggle(p)
    p.click("#intakeVoiceNotch")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakeVoiceFan')).opacity === '1'")
    p.wait_for_timeout(450)
    fan, toggle, status = _box(p, "#intakeVoiceFan"), _box(p, "#intakeToggle"), _box(p, "#status")
    assert not _overlaps(fan, toggle), (fan, toggle)
    assert not _overlaps(fan, status), (fan, status)
    # The toggle is still clickable where it's drawn.
    hit = p.evaluate(f"document.elementFromPoint({toggle['x'] + toggle['w'] / 2}, {toggle['y'] + toggle['h'] / 2})?.id")
    assert hit == "intakeToggle"
    p.click("#intakeVoiceNotch")


# ---------- 3. voice panel targets ----------

@pytest.mark.parametrize("width,height", [(390, 844), (360, 780)])
def test_voice_panel_items_are_44px_on_phones(open_page, width, height):
    p = open_page(width=width, height=height)
    p.click("#intakeVoiceNotch")
    p.wait_for_function("getComputedStyle(document.querySelector('#intakeVoiceFan')).opacity === '1'")
    p.wait_for_timeout(400)
    items = p.eval_on_selector_all("#intakeVoiceFan .voice-item", "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; })")
    assert len(items) == 3 and all(w >= 44 and h >= 44 for w, h in items), items
    fan, bar = _box(p, "#intakeVoiceFan"), _box(p, "#intakeComposer")
    for dock in ("#trayButton", "#cabinetButton"):
        assert not _overlaps(fan, _box(p, dock)), dock
    assert fan["x"] >= bar["x"] and fan["r"] <= bar["r"]
    # The glyph stays 16px.
    assert p.eval_on_selector("#intakeFanListen .icon svg", "e => Math.round(e.getBoundingClientRect().width)") == 16


# ---------- 4. tab order ----------

@pytest.mark.parametrize("textarea,run,mic", [("#questionnaire", "runAgent", "intakeMic"), ("#agentMessage", "runChat", "micButton")])
@pytest.mark.parametrize("width", [1280, 390])
def test_tab_goes_textarea_run_mic_and_run_stays_left_of_mic(open_page, textarea, run, mic, width):
    p = open_page(width=width, height=844)
    if textarea == "#agentMessage":
        p.evaluate("openAgent()")
        p.wait_for_selector(textarea, state="visible")
    p.fill(textarea, "Hello")
    p.wait_for_timeout(250)
    if p.get_attribute(f"#{run}", "hidden") is not None:  # Run only shows when it can act
        pytest.skip(f"{run} hidden in this state")
    p.focus(textarea)
    p.keyboard.press("Tab")
    first = p.evaluate("document.activeElement.id")
    p.keyboard.press("Tab")
    second = p.evaluate("document.activeElement.id")
    assert (first, second) == (run, mic)
    run_box, mic_box = _box(p, f"#{run}"), _box(p, f"#{mic}")
    assert run_box["r"] <= mic_box["x"]  # visual order unchanged: Run left of the mic


# ---------- 5. dark focus ring ----------

FOCUS = """(sel) => { const cs = getComputedStyle(document.querySelector(sel));
  return {color: cs.outlineColor, style: cs.outlineStyle,
          token: getComputedStyle(document.documentElement).getPropertyValue('--control-focus').trim(),
          ring: getComputedStyle(document.documentElement).getPropertyValue('--focus-ring').trim()}; }"""


def _hex_rgb(value):
    value = value.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


@pytest.mark.parametrize("selector", ["#inputsPromptDismiss", "#inputsPromptLink", "#loadSample"])
def test_dark_focus_ring_is_control_focus_not_gold(open_page, selector):
    p = open_page(theme="dark")
    p.evaluate("""() => { document.querySelector('#inputsPrompt').hidden = false; document.querySelector('#inputsPromptServices').hidden = false; }""")
    p.focus("#questionnaire")
    p.keyboard.press("Tab")  # keyboard modality, so :focus-visible applies to the next programmatic focus too
    p.focus(selector)
    assert p.evaluate("(s) => document.querySelector(s).matches(':focus-visible')", selector)
    look = p.evaluate(FOCUS, selector)
    assert look["style"] != "none"
    assert look["color"] == _hex_rgb(look["token"]) == "rgb(195, 200, 207)", look
    assert look["color"] != GOLD
    assert look["ring"].lower() in ("#c3c8cf", "var(--control-focus)")


def test_light_focus_ring_unchanged(open_page):
    p = open_page(theme="light")
    p.focus("#questionnaire")
    p.keyboard.press("Tab")
    p.focus("#loadSample")
    assert p.evaluate(FOCUS, "#loadSample")["color"] == "rgb(95, 102, 112)"


def test_mustard_only_on_drag_over_in_dark(open_page):
    p = open_page(theme="dark")
    ring = p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--focus-ring').trim().toLowerCase()")
    assert "dfa81c" not in ring and "c8a96e" not in ring


# ---------- 6. tips-card x hit area ----------

@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,touch", [(390, True), (360, True)])
def test_inputs_dismiss_has_44px_hit_area_and_same_look(open_page, theme, width, touch):
    p = open_page(theme=theme, width=width, height=844, touch=touch)
    p.evaluate("() => { const s = document.querySelector('#inputsPrompt'); s.hidden = false; s.scrollIntoView({block: 'center'}); }")
    p.wait_for_timeout(150)
    box = _box(p, "#inputsPromptDismiss")
    assert round(box["w"]) == 28 and round(box["h"]) == 28, box  # visual size unchanged
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    for dx, dy in [(0, 0), (-21, 0), (21, 0), (0, -21), (0, 21), (-21, -21), (21, 21), (-21, 21), (21, -21)]:
        hit = p.evaluate(f"document.elementFromPoint({cx + dx}, {cy + dy})?.closest('#inputsPromptDismiss')?.id")
        assert hit == "inputsPromptDismiss", (dx, dy)
    # The heading text is not covered by the hit area.
    title = _box(p, "#inputsPromptTitle")
    hit = p.evaluate(f"document.elementFromPoint({title['x'] + 10}, {title['y'] + title['h'] / 2})?.id")
    assert hit == "inputsPromptTitle"
