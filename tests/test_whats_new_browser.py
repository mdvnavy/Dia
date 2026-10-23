"""What's new indicator (spec proto/whats-new-indicator.md v0.3 §10), browser acceptance tests (Playwright).

v0.4.1: silver glow on the mark (--wn-glow-*), plus an aria-hidden NEW tag (.wn-tag) across the mark's bottom edge.
Dia only; the Tia half (test 19 and the Tia legs of 9 and 20-24) ships with Tia's letterhead branch.
Skipped when Playwright/Chromium is unavailable.
"""

import colorsys
import json
import os
import re
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY = "dia-whats-new-seen"
TAG_DARK, TAG_LIGHT = (255, 92, 119), (193, 16, 45)   # base reds of Glyph's clay NEW image (= --wn-tag-text-dark/-light)
GLYPH_CLAY_NEW = Path("/workspace/dia-tia/glyph/clay-new")   # Glyph's measuring files (halo-only renders), read only
GLYPH_BRAND = Path("/workspace/dia-tia/glyph/brand")         # ... and the 44px letter masks; never shipped in static/
NOTES = {
    "version": 1,
    "notes": [
        {"id": "2026-10-22-draft", "date": "2026-10-22", "title": "Draft note never shown", "body": "Draft.", "app": "dia", "live_flag": False},
        {"id": "2026-10-20-c", "date": "2026-10-20", "title": "Third live note", "body": "Body C.", "app": "dia", "live_flag": True},
        {"id": "2026-10-21-tia", "date": "2026-10-21", "title": "Tia only note", "body": "Tia.", "app": "tia", "live_flag": True},
        {"id": "2026-10-18-b", "date": "2026-10-18", "title": "Second live note", "body": "Body B.", "app": "dia", "live_flag": True},
        {"id": "2026-10-15-a", "date": "2026-10-15", "title": "First live note", "body": "Body A.", "app": "dia", "live_flag": True},
    ],
}
LATEST = "2026-10-20-c"
OLDEST = "2026-10-15-a"


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
        last_error = None
        for path in [os.environ.get("DIA_CHROME"), None, "/usr/bin/google-chrome", "/usr/bin/chromium"]:
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


class Session:
    def __init__(self, browser, base_url, *, theme="dark", width=1280, height=800, seen="__unset__", notes=NOTES,
                 status=200, raw=None, reduced_motion="reduce", forced_colors="none", dsf=1, storage_throws=False,
                 hold_json=False, contrast="no-preference"):
        self.base_url = base_url
        self.context = browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=dsf, reduced_motion=reduced_motion,
            forced_colors=forced_colors, contrast=contrast, color_scheme=theme, user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
        init = f"try {{ localStorage.setItem('dia-theme', {json.dumps(theme)}); }} catch (e) {{}}\n"
        if seen != "__unset__":
            init += f"if (!sessionStorage.getItem('wn-init')) {{ sessionStorage.setItem('wn-init', '1'); " \
                    f"localStorage.setItem({json.dumps(KEY)}, {json.dumps(seen)}); }}\n"
        else:
            init += f"if (!sessionStorage.getItem('wn-init')) {{ sessionStorage.setItem('wn-init', '1'); localStorage.removeItem({json.dumps(KEY)}); }}\n"
        if storage_throws:
            init += """
              (() => { const g = Storage.prototype.getItem, s = Storage.prototype.setItem;
                Storage.prototype.getItem = function (k) { if (k === 'dia-whats-new-seen') throw new DOMException('blocked', 'SecurityError'); return g.call(this, k); };
                Storage.prototype.setItem = function (k, v) { if (k === 'dia-whats-new-seen') throw new DOMException('blocked', 'SecurityError'); return s.call(this, k, v); };
              })();"""
        # Record when the notes fetch starts, to compare with first paint (acceptance test 16).
        init += """
          (() => { const f = window.fetch; window.__wnFetchAt = [];
            window.fetch = function (input, opts) { const u = String(input && input.url || input);
              if (u.includes('whats-new.json')) window.__wnFetchAt.push(performance.now()); return f.apply(this, arguments); };
            // First rendered frame: a rAF callback queued at DOMContentLoaded runs at the first paint opportunity.
            document.addEventListener('DOMContentLoaded', () => requestAnimationFrame(() => { window.__wnFrameAt = performance.now(); }));
          })();"""
        self.context.add_init_script(init)
        self.hits = 0
        self.status, self.raw, self.notes, self.hold = status, raw, notes, hold_json
        self.context.route("**/static/whats-new.json", self._serve)
        self.page = self.context.new_page()
        self.page.clock.set_fixed_time(1761240240000)  # 2026-10-23 13:24 ET, so screenshots match
        self.errors, self.console_errors = [], []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: m.type == "error" and self.console_errors.append(m.text))
        self.page.goto(base_url + "/")
        if not hold_json:
            self.ready()

    def _serve(self, route):
        self.hits += 1
        if self.hold:
            return  # never answer: the fetch stays pending
        if self.raw is not None:
            route.fulfill(status=self.status, body=self.raw, content_type="application/json")
        else:
            route.fulfill(status=self.status, body=json.dumps(self.notes), content_type="application/json")

    def ready(self):
        self.page.wait_for_function("document.querySelector('#whatsNewWrap').dataset.ready === '1'", timeout=8000)

    def reload(self):
        self.page.reload()
        self.ready()

    def ev(self, js, arg=None):
        return self.page.evaluate(js, arg)

    @property
    def state(self):
        return self.page.get_attribute("#whatsNew", "data-state")

    @property
    def seen(self):
        return self.ev(f"localStorage.getItem({json.dumps(KEY)})")

    def own_errors(self):
        # The browser's own network line for a 404 is not ours; everything else is.
        return self.errors + [m for m in self.console_errors if "Failed to load resource" not in m]

    def close(self):
        self.context.close()


@pytest.fixture
def session(browser, base_url):
    made = []

    def make(**kwargs):
        s = Session(browser, base_url, **kwargs)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def panel_open(s):
    return s.ev("!document.querySelector('#whatsNewPanel').hidden")


def wait_panel_rendered(s):
    s.page.wait_for_function("!document.querySelector('#whatsNewPanel').hasAttribute('aria-busy')")


# --- 1-8: behaviour -----------------------------------------------------------------------------------------------

def test_01_first_visit_no_glow_and_key_set(session):
    s = session()
    assert s.state == "none"
    assert s.seen == LATEST
    assert s.page.get_attribute("#whatsNew", "aria-label") == "What's new"


def test_02_returning_user_glows_with_count_and_tooltip(session):
    s = session(seen=OLDEST)
    assert s.state == "new"
    assert s.page.get_attribute("#whatsNew", "aria-label") == "What's new, 2 new updates"
    assert s.ev("document.querySelector('#whatsNewTip').textContent") == "What's new · 2 updates"
    assert s.ev("document.querySelector('#whatsNewCount').textContent") == "2 new updates"
    s.page.hover("#whatsNew")
    s.page.wait_for_function("document.querySelector('#whatsNewWrap').classList.contains('show-tip')", timeout=2000)
    one = session(seen="2026-10-18-b")
    assert one.page.get_attribute("#whatsNew", "aria-label") == "What's new, 1 new update"
    assert one.ev("document.querySelector('#whatsNewTip').textContent") == "What's new · 1 update"


@pytest.mark.parametrize("how", ["click", "enter", "space", "menu", "palette"])
def test_03_opening_clears_glow_writes_latest_and_survives_reload(session, how):
    s = session(seen=OLDEST)
    assert s.state == "new"
    p = s.page
    if how == "click":
        p.click("#whatsNew")
    elif how in ("enter", "space"):
        p.focus("#whatsNew")
        p.keyboard.press("Enter" if how == "enter" else " ")
    elif how == "menu":
        p.click("#navBrandMenu")
        p.click("#navDropdown [data-command='whats-new']")
    else:
        p.keyboard.press("Control+k")
        p.keyboard.type("release notes")
        assert "What's new" in p.eval_on_selector_all("#paletteList [role=option] span", "els => els.map(e => e.textContent)")
        p.keyboard.type("")
        p.fill("#paletteInput", "what's new")
        p.keyboard.press("Enter")
    assert panel_open(s)
    assert s.state == "none"
    assert s.seen == LATEST
    s.reload()
    assert s.state == "none"
    assert s.own_errors() == []


def test_04_new_pills_on_this_open_only(session):
    s = session(seen=OLDEST)
    s.page.click("#whatsNew")
    wait_panel_rendered(s)
    titles = s.ev("[...document.querySelectorAll('#whatsNewBody .wn-item h3')].map(n => n.textContent)")
    assert titles == ["Third live note", "Second live note", "First live note"]
    pills = s.ev("[...document.querySelectorAll('#whatsNewBody .wn-item')].map(n => !!n.querySelector('.wn-new'))")
    assert pills == [True, True, False]
    s.page.keyboard.press("Escape")
    s.page.click("#whatsNew")
    assert s.ev("document.querySelectorAll('#whatsNewBody .wn-new').length") == 0


def test_05_draft_note_never_renders_counts_or_glows(session):
    s = session(seen=OLDEST)
    assert s.page.get_attribute("#whatsNew", "aria-label").endswith("2 new updates")  # not 3
    s.page.click("#whatsNew")
    text = s.ev("document.querySelector('#whatsNewBody').textContent")
    assert "Draft note never shown" not in text and "Tia only note" not in text
    # Only a draft is newer than the stored id: no glow.
    only_draft = {"version": 1, "notes": [NOTES["notes"][0], NOTES["notes"][1]]}
    t = session(seen=LATEST, notes=only_draft)
    assert t.state == "none"


@pytest.mark.parametrize("case", ["404", "invalid", "zero-live"])
def test_06_failures_no_glow_and_error_or_empty_state_with_retry(session, case):
    kwargs = {"seen": OLDEST}
    if case == "404":
        kwargs.update(status=404, raw='{"error": "not found"}')
    elif case == "invalid":
        kwargs.update(raw="{not json")
    else:
        kwargs.update(notes={"version": 1, "notes": [NOTES["notes"][0]]})
    s = session(**kwargs)
    assert s.state == "none"
    s.page.click("#whatsNew")
    wait_panel_rendered(s)
    body = s.ev("document.querySelector('#whatsNewBody').textContent")
    if case == "zero-live":
        assert "Nothing new yet." in body
    else:
        assert "Release notes couldn't load. Try again." in body
        hits = s.hits
        s.status, s.raw = 200, None
        s.page.click("#whatsNewBody .wn-retry")
        s.page.wait_for_function("document.querySelectorAll('#whatsNewBody .wn-item').length === 3")
        assert s.hits == hits + 1
        assert s.state == "none"
    assert s.own_errors() == []


def test_07_storage_throws_no_glow_panel_works_no_console_error(session):
    s = session(seen=OLDEST, storage_throws=True)
    assert s.state == "none"
    s.page.click("#whatsNew")
    wait_panel_rendered(s)
    assert s.ev("document.querySelectorAll('#whatsNewBody .wn-item').length") == 3
    assert s.errors == [] and s.console_errors == []


def test_08_stored_id_missing_no_glow_and_key_rewritten(session):
    s = session(seen="2026-01-01-retracted")
    assert s.state == "none"
    assert s.seen == LATEST


# --- 9-11, 23: motion and forced colours ---------------------------------------------------------------------------

INDICATOR_ANIMS = """() => document.getAnimations().filter(a => a.effect && a.effect.target &&
    document.querySelector('#whatsNewWrap').contains(a.effect.target)).map(a => ({
      name: a.animationName, pseudo: a.effect.pseudoElement, state: a.playState,
      iterations: a.effect.getTiming().iterations, duration: a.effect.getTiming().duration }))"""


TAG = """() => { const t = document.querySelector('#whatsNew .wn-tag'), c = getComputedStyle(t);
  return { display: c.display, opacity: c.opacity, anim: c.animationName, color: c.color, bg: c.backgroundColor,
           shadow: c.boxShadow, border: c.borderTopStyle + ' ' + c.borderTopWidth, hidden: t.getAttribute('aria-hidden') }; }"""
MARK_SHADOW = "getComputedStyle(document.querySelector('.wn-mark')).boxShadow"
HAIRLINE = """(() => { const d = document.createElement('div'); d.style.boxShadow = '0 0 0 1px var(--border)';
  document.body.append(d); const v = getComputedStyle(d).boxShadow; d.remove(); return v; })()"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_09_reduced_motion_no_animation_glow_and_tag_present(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, reduced_motion="reduce")
    assert s.state == "new"
    s.page.wait_for_timeout(100)
    assert s.ev(INDICATOR_ANIMS) == []
    assert s.ev(MARK_SHADOW) != s.ev(HAIRLINE)            # glow lit
    tag = s.ev(TAG)
    assert tag["display"] == "block" and tag["opacity"] == "1" and tag["anim"] == "none"


def test_10_fade_runs_once_then_nothing_animates(session):
    s = session(seen=OLDEST, reduced_motion="no-preference")
    anims = s.ev(INDICATOR_ANIMS)
    assert sorted(a["name"] for a in anims) == ["wn-fade-in", "wn-tag-in"]   # glow and tag fade together
    assert all(a["iterations"] == 1 and a["duration"] == 1200 for a in anims)
    s.page.wait_for_timeout(1400)
    assert all(a["state"] == "finished" for a in s.ev(INDICATOR_ANIMS))
    s.page.hover("#whatsNew")
    s.page.focus("#whatsNew")
    s.page.wait_for_timeout(100)
    after = s.ev(INDICATOR_ANIMS)
    assert all(a["state"] != "running" for a in after), after   # never re-runs on hover or focus
    assert len(after) <= 2


FORCED = """() => { const b = document.querySelector('#whatsNew'), img = b.querySelector('.wn-mark'), tag = b.querySelector('.wn-tag');
  const sys = (v) => { const p = document.createElement('span'); p.style.color = v; document.body.append(p);
    const out = getComputedStyle(p).color; p.remove(); return out; };
  const c = getComputedStyle(img), t = getComputedStyle(tag), tt = getComputedStyle(b.querySelector('.wn-tag-text'));
  const imgs = [...b.querySelectorAll('.wn-tag-img')].map((i) => getComputedStyle(i).display);
  return { style: c.outlineStyle, width: c.outlineWidth, offset: c.outlineOffset, color: c.outlineColor, hl: sys('Highlight'),
           shadow: c.boxShadow, tagDisplay: t.display, tagBg: t.backgroundColor, tagFg: tt.color, textDisplay: tt.display, imgs,
           textFont: [tt.fontSize, tt.fontWeight, tt.textTransform, tt.letterSpacing, tt.lineHeight], tagBorder: [t.borderTopStyle, t.borderTopWidth, t.borderTopColor],
           canvas: sys('Canvas'), canvasText: sys('CanvasText') }; }"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_11_24_forced_colors_highlight_outline_and_system_tag(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, forced_colors="active")
    assert s.state == "new"
    f = s.ev(FORCED)
    assert f["style"] == "solid" and f["width"] == "2px" and f["offset"] == "0px" and f["color"] == f["hl"]
    assert f["shadow"] == "none"
    assert f["tagDisplay"] == "block" and f["textDisplay"] == "block" and f["tagFg"] == f["canvasText"]
    assert f["imgs"] == ["none", "none"]          # the fixed-colour clay image gives way to CanvasText text
    assert f["textFont"] == ["9px", "700", "uppercase", "0.36px", "9px"]
    # the tag text box clears the 2px outline (offset-y becomes 2px in forced colours)
    gap = s.ev("document.querySelector('#whatsNew .wn-tag').getBoundingClientRect().top - document.querySelector('.wn-mark').getBoundingClientRect().bottom")
    assert abs(gap - 2) < 0.01
    rest = session(theme=theme, width=width, forced_colors="active")
    assert rest.state == "none"
    r = rest.ev(FORCED)
    assert r["style"] == "none" and r["tagDisplay"] == "none"


# --- 12-14, 22: geometry --------------------------------------------------------------------------------------------

def _lum(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _rgb(text):
    nums = [float(x) for x in re.findall(r"[\d.]+", text)]
    return nums[:3] + ([nums[3]] if len(nums) > 3 else [1.0])


def _hex(text):
    text = text.strip().lstrip("#")
    return [int(text[i:i + 2], 16) for i in (0, 2, 4)]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_12_hit_area_and_focus_ring_contrast(session, theme, width):
    s = session(seen=LATEST, theme=theme, width=width)   # rest state: the inset outline ring
    assert s.state == "none"
    box = s.page.locator("#whatsNew").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    s.page.focus("#navBrandMenu")
    # Tab through the left corner to the mark (DOM order = visual order).
    for _ in range(4):
        if s.ev("document.activeElement.id") == "whatsNew":
            break
        s.page.keyboard.press("Tab")
    assert s.ev("document.activeElement.id") == "whatsNew"
    ring = s.ev("(() => { const c = getComputedStyle(document.activeElement); return [c.outlineStyle, c.outlineWidth, c.outlineColor]; })()")
    assert ring[0] == "solid" and ring[1] == "2px"
    bg = _hex(s.ev("getComputedStyle(document.documentElement).getPropertyValue('--bg')"))
    l1, l2 = sorted([_lum(_rgb(ring[2])), _lum(bg)], reverse=True)
    assert (l1 + 0.05) / (l2 + 0.05) >= 3.0


def ink(s):
    """Ink box (CSS px, viewport coords) of the NEW tag: the bbox of its red pixels (the clay letters) in a DPR-scaled
    shot of the button. Nothing else in the indicator is red (test 20), and the image's bg-coloured seat isn't ink."""
    from io import BytesIO
    from PIL import Image
    s.page.wait_for_function("[...document.querySelectorAll('#whatsNewTag img')].every((i) => i.complete)")
    dsf = s.ev("devicePixelRatio")
    b = s.page.locator("#whatsNew").bounding_box()
    clip = {"x": b["x"], "y": b["y"], "width": b["width"], "height": b["height"]}   # the ink sits inside the button
    shot = Image.open(BytesIO(s.page.screenshot(clip=clip, scale="device"))).convert("RGB")
    px, (w, h) = shot.load(), shot.size
    pts = [(x, y) for y in range(h) for x in range(w) if px[x, y][0] > px[x, y][1] + 60 and px[x, y][0] > px[x, y][2] + 30]
    assert pts, "no NEW ink found"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [clip["x"] + min(xs) / dsf, clip["y"] + min(ys) / dsf, clip["x"] + (max(xs) + 1) / dsf, clip["y"] + (max(ys) + 1) / dsf], pts


FOCUS_NEW = """() => { const b = document.querySelector('#whatsNew'), r = b.getBoundingClientRect(), c = getComputedStyle(b, '::before');
  return { outline: getComputedStyle(b).outlineStyle, content: c.content, border: [c.borderTopStyle, c.borderTopWidth, c.borderTopColor],
           top: r.top + parseFloat(c.top), bottom: r.bottom - parseFloat(c.bottom), left: r.left + parseFloat(c.left), right: r.right - parseFloat(c.right),
           band: document.querySelector('header.top-nav').getBoundingClientRect().bottom }; }"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_12_new_state_focus_ring_clears_new_text_and_stays_in_band(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, height=844, dsf=4)
    box, _ = ink(s)
    s.page.focus("#navBrandMenu")
    for _ in range(4):
        if s.ev("document.activeElement.id") == "whatsNew":
            break
        s.page.keyboard.press("Tab")
    f = s.ev(FOCUS_NEW)
    assert f["outline"] == "none" and f["content"] == '""' and f["border"][:2] == ["solid", "2px"]
    btn = s.page.locator("#whatsNew").bounding_box()
    assert abs(f["bottom"] - (btn["y"] + btn["height"] + 2)) < 0.01      # bottom edge 2px below the button
    inner_bottom = f["bottom"] - 2
    tag = s.page.locator("#whatsNew .wn-tag").bounding_box()
    assert inner_bottom - (tag["y"] + tag["height"]) >= 1, (inner_bottom, tag)   # >= 1px clear of the tag box (spec 2px)
    assert inner_bottom - box[3] >= 1, (inner_bottom, box)              # >= 1px clear of the ink (spec measured 2px)
    assert f["bottom"] <= f["band"]                                       # inside the 52/56px band
    bg = _hex(s.ev("getComputedStyle(document.documentElement).getPropertyValue('--bg')"))
    l1, l2 = sorted([_lum(_rgb(f["border"][2])), _lum(bg)], reverse=True)
    assert (l1 + 0.05) / (l2 + 0.05) >= 3.0


CLOCK = """() => { const c = document.querySelector('#lhClock').getBoundingClientRect(); return c.left + c.width / 2; }"""


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_13_clock_does_not_move_and_band_heights(session, theme):
    s = session(seen=OLDEST, theme=theme)
    with_btn = s.ev(CLOCK)
    band = s.page.locator("header.top-nav").bounding_box()
    assert abs(with_btn - (band["x"] + band["width"] / 2)) <= 0.5
    s.ev("document.querySelector('#whatsNewWrap').remove()")
    assert abs(s.ev(CLOCK) - with_btn) <= 0.5
    assert band["height"] == 52
    m = session(seen=OLDEST, theme=theme, width=390, height=844)
    assert m.page.locator("header.top-nav").bounding_box()["height"] == 56


def test_14_mobile_one_line_and_360_mark_before_search(session):
    s = session(seen=OLDEST, width=390, height=844)
    band = s.page.locator("header.top-nav").bounding_box()
    trig = s.page.locator("#navBrandMenu").bounding_box()
    assert s.page.locator("#navBrandMenu").inner_text().strip() == "DIA"
    assert trig["height"] <= 44 and trig["y"] >= band["y"] and trig["y"] + trig["height"] <= band["y"] + band["height"]
    name = s.page.locator("#navBrandMenu").evaluate("b => b.getAttribute('aria-label') || b.textContent")
    assert "DIA" in name
    for sel in ("#whatsNew", "#paletteTrigger", "#themeToggle", "#lhTime"):
        r = s.page.locator(sel).bounding_box()
        assert r["y"] >= band["y"] - 0.5 and r["y"] + r["height"] <= band["y"] + band["height"] + 0.5, sel
    n = session(seen=OLDEST, width=360, height=780)
    assert n.ev("getComputedStyle(document.querySelector('#lhClock')).display") == "none"
    mark = n.page.locator("#whatsNew").bounding_box()
    search = n.page.locator("#paletteTrigger").bounding_box()
    assert mark["x"] + mark["width"] <= search["x"] + 0.5
    assert search["x"] - (mark["x"] + mark["width"]) <= 24
    n.page.click("#whatsNew")
    assert panel_open(n)


GEOM = """() => { const btn = document.querySelector('#whatsNew').getBoundingClientRect();
  const img = document.querySelector('.wn-mark').getBoundingClientRect();
  const tag = document.querySelector('#whatsNew .wn-tag').getBoundingClientRect();
  let right = -Infinity;
  for (const id of ['lhTime', 'lhDate']) { const el = document.getElementById(id);
    if (getComputedStyle(el).display === 'none') continue;
    const r = document.createRange(); r.selectNodeContents(el);
    for (const rect of r.getClientRects()) right = Math.max(right, rect.right); }
  const clipped = []; let n = document.querySelector('#whatsNew');
  while (n && !n.matches('header.top-nav')) { const c = getComputedStyle(n);
    if (c.overflowX !== 'visible' || c.overflowY !== 'visible' || c.clipPath !== 'none' || /paint|strict|content/.test(c.contain)) clipped.push(n.className || n.id);
    n = n.parentElement; }
  const band = getComputedStyle(document.querySelector('header.top-nav'));
  return { btn: [btn.left, btn.top, btn.right, btn.bottom], img: [img.left, img.top, img.right, img.bottom],
           tag: [tag.left, tag.top, tag.right, tag.bottom, tag.width, tag.height],
           ringGap: (img.left - 1) - right, tagGap: tag.left - right, clipped, band: [band.overflowX, band.overflowY] }; }"""

# Green cursor underscore in Glyph's clay mark, in 22px mark coordinates (measured from clay-mark-88.png rows 51-56).
CURSOR_Y = (12.75, 14.25)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_23_target_ring_clearance_and_no_clipping(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, height=844)
    s.page.clock.set_fixed_time("2026-10-23T14:59:00-04:00")  # 2026-10-23 14:59 ET: a wide time string
    s.page.wait_for_timeout(50)
    g = s.ev(GEOM)
    bl, bt, br, bb = g["btn"]
    il, it, ir, ib = g["img"]
    assert abs((br - bl) - 44) < 0.5 and abs((bb - bt) - 44) < 0.5
    assert g["ringGap"] >= 8, g                                            # glow ring clears the clock text
    assert g["clipped"] == [] and g["band"] == ["visible", "visible"]


CLOCK_RIGHT = """() => { let right = -Infinity;
  for (const id of ['lhTime', 'lhDate']) { const el = document.getElementById(id);
    if (getComputedStyle(el).display === 'none') continue;
    const r = document.createRange(); r.selectNodeContents(el);
    for (const rect of r.getClientRects()) right = Math.max(right, rect.right); }
  return right; }"""
MEASURED = {}


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_23_new_ink_under_mark_and_clear_of_clock(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, height=844, dsf=4)
    s.page.clock.set_fixed_time("2026-10-23T14:59:00-04:00")
    s.page.wait_for_timeout(50)
    (il, it, ir, ib), _ = ink(s)
    g = s.ev(GEOM)
    bl, bt, br, bb = g["btn"]
    ml, mt, mr, mb = g["img"]
    clock = s.ev(CLOCK_RIGHT)
    MEASURED[(theme, width)] = {"ink_to_clock": round(il - clock, 2), "ring_to_clock": round(ml - 1 - clock, 2),
                                "mark_to_ink": round(it - mb, 2), "ink_in_button_bottom": round(bb - ib, 2),
                                "ink": (round(ir - il, 2), round(ib - it, 2))}
    print(MEASURED[(theme, width)])
    assert it - (mb + 1) >= 0.75, (it, mb)                 # letters start ~1px below the 1px glow ring: no overlap
    assert il >= bl and ir <= br and it >= bt and ib <= bb  # ink inside the 44x44 button
    assert il - clock >= 10, (il, clock)                   # ink -> clock text >= 10px (spec: 12.45 / 13.45)
    assert ml - 1 - clock >= 8
    assert abs((ir - il) - 20) <= 1 and abs((ib - it) - 6.67) <= 0.75  # clay letters ~20 x 6.7 inside the 22x8 image
    t = s.page.locator("#whatsNew .wn-tag").bounding_box()
    assert abs(t["width"] - 22) < 0.01 and abs(t["height"] - 8) < 0.01
    assert abs(t["y"] - (mb + 1)) < 0.01                                 # image box 1px under the mark (--wn-tag-offset-y)
    assert abs((t["x"] + 11) - (ml + mr) / 2) <= 0.5                     # centred under the mark


CLOCK_RECTS = """() => { const out = [];
  for (const id of ['lhTime', 'lhDate']) { const el = document.getElementById(id);
    if (getComputedStyle(el).display === 'none') continue;
    const r = document.createRange(); r.selectNodeContents(el);
    for (const b of r.getClientRects()) out.push([b.left, b.top, b.right, b.bottom]); }
  return out; }"""


def _shot(s, clip):
    from io import BytesIO
    from PIL import Image
    return Image.open(BytesIO(s.page.screenshot(clip=clip, scale="device"))).convert("RGB")


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("dsf", [1, 2, 3])
def test_23_bloom_at_clock_empty_space_le_4_and_glyphs_noise_only(session, theme, width, dsf):
    """Spec v0.5.1 test 23: inside the clock's text box the visible bloom (glow on vs off, per channel) may reach <= 4/255
    in empty space; clock glyph pixels rise by <= 1/255 (noise); clock text keeps >= 6.82:1 light / 7.95:1 dark."""
    s = session(seen=OLDEST, theme=theme, width=width, height=844, dsf=dsf)
    s.page.clock.set_fixed_time("2026-10-23T14:59:00-04:00")
    s.page.wait_for_timeout(50)
    rects = s.ev(CLOCK_RECTS)
    l, t = min(r[0] for r in rects), min(r[1] for r in rects)
    clip = {"x": l, "y": t, "width": max(r[2] for r in rects) - l, "height": max(r[3] for r in rects) - t}
    state = "document.querySelector('#whatsNew').dataset.state = %s"
    on = _shot(s, clip)
    s.ev(state % "'none'"); off = _shot(s, clip)
    s.ev(state % "'new'"); again = _shot(s, clip)
    assert list(again.getdata()) == list(on.getdata())                 # renders are deterministic: diffs are the glow
    s.ev(state % "'none'")
    s.ev("document.getElementById('lhClock').style.color = 'transparent';"
         "for (const id of ['lhTime','lhDate']) document.getElementById(id).style.color = 'transparent'")
    bare = _shot(s, clip)
    s.ev(state % "'new'")
    a, b, c = on.load(), off.load(), bare.load()
    w, h = on.size
    d = lambda p, q: max(abs(p[i] - q[i]) for i in range(3))
    ink = {(x, y): d(b[x, y], c[x, y]) for y in range(h) for x in range(w)}
    glyph = {p for p, v in ink.items() if v > 0}                      # any pixel the clock text paints
    empty = [d(a[x, y], b[x, y]) for (x, y) in ink if (x, y) not in glyph]
    raised = {}
    for thr in (1, 8, 32):
        vals = [d(a[p], b[p]) for p in glyph if ink[p] >= thr]
        raised[thr] = max(vals or [0])
    top = max(ink.values())
    core = [p for p in glyph if ink[p] >= 0.9 * top]                    # fully inked glyph pixels
    worst = min(_contrast(a[p], c[p]) for p in core)
    MEASURED[("bloom", theme, width, dsf)] = {"empty_max": max(empty or [0]), "glyph_raise_by_ink>=1/8/32": raised,
                                              "clock_min_contrast": round(worst, 2)}
    print(MEASURED[("bloom", theme, width, dsf)])
    assert glyph and core
    assert max(empty or [0]) <= 4                                       # empty space inside the clock's text box
    assert raised[1] <= 1, raised     # spec v0.5.1: clock glyph pixels may rise by at most 1/255 (measurement noise)
    assert worst >= (6.82 if theme == "light" else 7.95), worst


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390, 360])
def test_23_button_top_snapped_to_whole_pixel(session, theme, width):
    """Spec v0.5.1: the button's top edge sits on a whole pixel (it was y 3.5 at 1280, 5.5 at 390), so the mark and the
    NEW image render sharp at DPR 1; it stays centred in the band and the mark/tag move with it."""
    s = session(seen=OLDEST, theme=theme, width=width, height=844)
    g = s.ev("""() => { const r = (q) => document.querySelector(q).getBoundingClientRect();
      const b = r('#whatsNew'), m = r('.wn-mark'), t = r('#whatsNew .wn-tag'), h = r('header.top-nav');
      return { top: b.top, markTop: m.top, tagTop: t.top, bandMid: h.top + h.height / 2, mid: b.top + b.height / 2 }; }""")
    assert g["top"] == int(g["top"]) and g["markTop"] == int(g["markTop"]) and g["tagTop"] == int(g["tagTop"]), g
    assert abs(g["mid"] - g["bandMid"]) <= 0.5, g


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_23_cursor_underscore_pixels_visible_with_tag(session, theme):
    """Pixel check: the green underscore is fully visible with NEW lit (DPR 4 crop of the mark)."""
    from io import BytesIO
    from PIL import Image
    s = session(seen=OLDEST, theme=theme, dsf=4)
    s.page.wait_for_function("document.querySelector('.wn-mark').complete")
    img = s.page.locator(".wn-mark").bounding_box()
    tag = s.page.locator("#whatsNew .wn-tag").bounding_box()
    crop = Image.open(BytesIO(s.page.screenshot(clip={"x": img["x"], "y": img["y"], "width": img["width"],
                                                       "height": tag["y"] - img["y"]}, scale="device"))).convert("RGB")
    green = sum(1 for p in crop.getdata() if p[1] > p[0] + 40 and p[1] > p[2] + 20)
    assert green >= 8, green


# --- 15-16: panel and loading ---------------------------------------------------------------------------------------

def test_15_panel_escape_close_outside_click_and_mobile_trap(session):
    s = session(seen=OLDEST)
    p = s.page
    p.click("#whatsNew")
    assert p.get_attribute("#whatsNew", "aria-expanded") == "true"
    assert s.ev("document.activeElement.id") == "whatsNewTitle"
    assert p.get_attribute("#whatsNewPanel", "role") == "dialog"
    assert p.get_attribute("#whatsNewPanel", "aria-modal") is None
    p.keyboard.press("Escape")
    assert not panel_open(s) and s.ev("document.activeElement.id") == "whatsNew"
    assert p.get_attribute("#whatsNew", "aria-expanded") == "false"
    p.click("#whatsNew")
    p.click("#whatsNewClose")
    assert not panel_open(s) and s.ev("document.activeElement.id") == "whatsNew"
    p.click("#whatsNew")
    p.mouse.click(200, 500)
    assert not panel_open(s)
    # Desktop: Tab past the end closes it and continues to Search actions.
    p.click("#whatsNew")
    p.keyboard.press("Tab")          # title -> close button (the only control with static notes)
    p.keyboard.press("Tab")
    assert not panel_open(s) and s.ev("document.activeElement.id") == "paletteTrigger"
    m = session(seen=OLDEST, width=390, height=844)
    m.page.click("#whatsNew")
    assert m.page.get_attribute("#whatsNewPanel", "aria-modal") == "true"
    for _ in range(6):
        m.page.keyboard.press("Tab")
        assert m.ev("document.querySelector('#whatsNewPanel').contains(document.activeElement)")
    for _ in range(3):
        m.page.keyboard.press("Shift+Tab")
        assert m.ev("document.querySelector('#whatsNewPanel').contains(document.activeElement)")
    m.page.keyboard.press("Escape")
    assert not panel_open(m) and m.ev("document.activeElement.id") == "whatsNew"


def test_16_fetch_after_first_paint_and_never_blocks_intake(session):
    s = session(seen=OLDEST)
    # headless shell may not emit paint entries: fall back to the first rAF frame after DOMContentLoaded
    fcp = s.ev("""(() => { const p = performance.getEntriesByType('paint'); const e = p.find(x => x.name === 'first-paint') || p[0];
      return e ? e.startTime : (window.__wnFrameAt ?? null); })()""")
    fetched = s.ev("window.__wnFetchAt")
    assert len(fetched) == 1 and fcp is not None and fetched[0] >= fcp
    h = session(seen=OLDEST, hold_json=True)
    h.page.wait_for_function("document.querySelector('#agentStatus').textContent !== 'checking...'")
    h.page.click("#loadSample")
    h.page.wait_for_function("questionnaire.value.length > 0")
    h.page.click("#runAgent")
    h.page.wait_for_function("statusText.textContent.startsWith('Complete')", timeout=8000)
    assert h.state == "none"
    h.page.click("#whatsNew")  # opens straight away with skeleton rows while the fetch is pending
    assert h.page.get_attribute("#whatsNewPanel", "aria-busy") == "true"
    assert h.ev("document.querySelectorAll('#whatsNewBody .wn-skel li').length") == 2
    assert h.own_errors() == []


# --- 20-21: tokens, assets, no red ----------------------------------------------------------------------------------

SILVER = {   # Glyph's stronger glow, 2:33 PM ET
    "dark": "rgba(217, 220, 224, 0.9) 0px 0px 0px 1px, rgba(217, 220, 224, 0.55) 0px 0px 13px 1.3px",
    "light": "rgb(124, 130, 138) 0px 0px 0px 1px, rgba(163, 169, 177, 0.72) 0px 0px 12px 1.3px",
}


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_20_mark_box_shadow_matches_silver_tokens(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width)
    assert s.ev(MARK_SHADOW) == SILVER[theme]
    src = s.ev("document.querySelector('.wn-mark').getAttribute('src')")
    s.page.click("#whatsNew")                     # opening clears the glow; the image itself never changes
    assert s.ev(MARK_SHADOW) == s.ev(HAIRLINE)
    assert s.ev("document.querySelector('.wn-mark').getAttribute('src')") == src == "/static/brand/clay-mark-44.png"


COLOURS = """() => { const out = []; const els = [...document.querySelectorAll('#whatsNewWrap, #whatsNewWrap *, #whatsNewPanel, #whatsNewPanel *')];
  for (const el of els) for (const pseudo of [null, '::after', '::before']) { const c = getComputedStyle(el, pseudo);
    if (pseudo && c.content === 'none') continue;
    if (!pseudo && c.display === 'none') continue;
    const props = ['color', 'backgroundColor', 'boxShadow'];
    for (const side of ['Top', 'Right', 'Bottom', 'Left'])
      if (c['border' + side + 'Style'] !== 'none' && parseFloat(c['border' + side + 'Width']) > 0) props.push('border' + side + 'Color');
    if (c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0) props.push('outlineColor');
    if (c.textDecorationLine !== 'none') props.push('textDecorationColor');
    for (const p of props) out.push([el.id || el.className || el.tagName, el.classList ? [...el.classList] : [], pseudo, p, c[p]]); }
  return out; }"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_20_red_only_as_tag_text_and_no_mustard(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width)
    s.page.click("#whatsNew")   # panel open with its "New" pills
    wait_panel_rendered(s)
    s.ev("document.querySelector('#whatsNew').dataset.state = 'new'")  # and the glow + tag lit at the same time
    bad, tag_red = [], []
    for who, classes, pseudo, prop, value in s.ev(COLOURS):
        for m in re.findall(r"rgba?\([^)]*\)", value or ""):
            r, g, b, a = _rgb(m)
            if a == 0:
                continue
            h, l, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            hue = h * 360
            reddish = sat > 0.35 and 0.15 < l < 0.9 and (hue <= 25 or hue >= 320)
            goldish = sat > 0.35 and 0.15 < l < 0.9 and 35 <= hue <= 50
            if reddish and ("wn-tag" in classes or "wn-tag-text" in classes) and pseudo is None and prop == "color":
                tag_red.append(m)
            elif reddish or goldish or (round(r), round(g), round(b)) == (223, 168, 28):
                bad.append((who, pseudo, prop, m))
    assert bad == []
    assert tag_red == []   # with the clay image in use, no CSS red is painted at all (the text fallback is display: none)
    # and the only red pixels in the band are inside the NEW image box
    from io import BytesIO
    from PIL import Image
    s.page.click("#whatsNewClose") if s.page.locator("#whatsNewPanel").is_visible() else None
    s.ev("document.querySelector('#whatsNew').dataset.state = 'new'")
    band = s.page.locator("header.top-nav").bounding_box()
    tag = s.page.locator("#whatsNew .wn-tag").bounding_box()
    shot = Image.open(BytesIO(s.page.screenshot(clip=band))).convert("RGB")
    px = shot.load()
    stray = [(x, y) for y in range(shot.size[1]) for x in range(shot.size[0])
             if px[x, y][0] > px[x, y][1] + 60 and px[x, y][0] > px[x, y][2] + 30
             and not (tag["x"] - 1 <= band["x"] + x <= tag["x"] + tag["width"] + 1 and tag["y"] - 1 <= band["y"] + y <= tag["y"] + tag["height"] + 1)]
    assert stray == []


def _contrast(a, b):
    l1, l2 = sorted([_lum(a), _lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


TAG_IMGS = """() => [...document.querySelectorAll('#whatsNewTag .wn-tag-img')].map((i) => ({ cls: i.className,
  display: getComputedStyle(i).display, src: i.currentSrc.split('/').pop(), w: i.getBoundingClientRect().width,
  h: i.getBoundingClientRect().height, alt: i.alt, complete: i.complete && i.naturalWidth > 0 }))"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("dsf", [1, 2, 3])
def test_22_clay_new_image_per_theme_and_dpr(session, theme, width, dsf):
    s = session(seen=OLDEST, theme=theme, width=width, height=844, dsf=dsf)
    s.page.wait_for_function("[...document.querySelectorAll('#whatsNewTag img')].every((i) => i.complete)")
    t = s.ev(TAG)
    assert t["display"] == "block" and t["hidden"] == "true" and t["anim"] == "none"   # static under reduced motion
    assert t["bg"] == "rgba(0, 0, 0, 0)" and t["shadow"] == "none" and t["border"].startswith("none")   # no pill
    imgs = {i["cls"].split("--")[-1]: i for i in s.ev(TAG_IMGS)}
    shown, other = imgs[theme], imgs["light" if theme == "dark" else "dark"]
    assert shown["display"] == "block" and other["display"] == "none"
    assert shown["src"] == f"clay-new-{theme}-{22 * dsf}.png" and shown["complete"]
    assert (shown["w"], shown["h"]) == (22, 8) and shown["alt"] == "" and other["alt"] == ""
    assert s.ev("getComputedStyle(document.querySelector('#whatsNew .wn-tag-text')).display") == "none"


def test_22_theme_toggle_swaps_clay_new_variant(session):
    s = session(seen=OLDEST, theme="dark")
    s.page.click("#themeToggle")
    assert s.ev("document.documentElement.dataset.theme") == "light"
    imgs = {i["cls"].split("--")[-1]: i["display"] for i in s.ev(TAG_IMGS)}
    assert imgs == {"light": "block", "dark": "none"}


@pytest.mark.skipif(not (GLYPH_CLAY_NEW / "clay-new-dark-44-haloonly.png").exists(),
                    reason="Glyph's 44px measuring files not on this machine")
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_22_new_letters_contrast_over_glow(session, theme, width):
    """Glyph's 44px method at DPR 2: render the page with the halo-only asset (seat, no letters) in place of the NEW image,
    then compare the base red with the backdrop under every letter pixel of Glyph's 44px ink mask (alpha >= 128).
    Must be >= 4.5:1 (Glyph: 5.41 light / 6.43 dark)."""
    import base64
    from io import BytesIO
    from PIL import Image
    s = session(seen=OLDEST, theme=theme, width=width, height=844, dsf=2)
    s.page.clock.set_fixed_time("2026-10-23T14:59:00-04:00")   # the clock's text width sets the mark's x
    s.page.wait_for_timeout(50)
    halo = "data:image/png;base64," + base64.b64encode((GLYPH_CLAY_NEW / f"clay-new-{theme}-44-haloonly.png").read_bytes()).decode()
    s.ev("""(src) => new Promise((ok) => { const i = [...document.querySelectorAll('#whatsNewTag img')].find((x) => getComputedStyle(x).display !== 'none');
      i.onload = () => ok(); i.srcset = src + ' 2x'; i.src = src; })""", halo)
    t = s.page.locator("#whatsNew .wn-tag").bounding_box()
    assert t["y"] == int(t["y"])                                   # snapped vertically (spec v0.5.1)
    x = round(t["x"] * 2) / 2
    assert abs(x - t["x"]) <= 0.06                                 # 1280: the clock's text width leaves x at .05
    under = Image.open(BytesIO(s.page.screenshot(clip={"x": x, "y": t["y"], "width": 22, "height": 8}, scale="device"))).convert("RGB")
    mask = Image.open(GLYPH_BRAND / f"clay-new-{theme}-44-mask.png").getchannel("A")
    assert under.size == mask.size == (44, 16)
    base = TAG_DARK if theme == "dark" else TAG_LIGHT
    u, m = under.load(), mask.load()
    pts = [(x, y) for y in range(16) for x in range(44) if m[x, y] >= 128]
    ratios = sorted((_contrast(base, u[p]), u[p]) for p in pts)
    worst, worst_bg = ratios[0]
    MEASURED[("contrast", theme, width)] = round(worst, 2)
    print({"theme": theme, "width": width, "pixels": len(pts), "min": round(worst, 2), "worst_bg": "#%02X%02X%02X" % worst_bg,
           "median": round(ratios[len(ratios) // 2][0], 2)})
    assert len(pts) == 382 and worst >= 4.5


@pytest.mark.parametrize("mode", ["contrast-more", "forced-colors"])
def test_22_tag_drops_1px_in_contrast_and_forced_modes(session, mode):
    kw = {"contrast": "more"} if mode == "contrast-more" else {"forced_colors": "active"}
    s = session(seen=OLDEST, **kw)
    assert s.ev("getComputedStyle(document.querySelector('#whatsNew')).getPropertyValue('--wn-tag-offset-y').trim()") == "2px"
    gap = s.ev("document.querySelector('#whatsNew .wn-tag').getBoundingClientRect().top - document.querySelector('.wn-mark').getBoundingClientRect().bottom")
    assert abs(gap - 2) < 0.01
    if mode == "contrast-more":
        assert s.ev(MARK_SHADOW).startswith(s.ev("(() => { const d = document.createElement('div'); d.style.color = 'var(--text)'; document.body.append(d); const v = getComputedStyle(d).color; d.remove(); return v; })()") + " 0px 0px 0px 2px")


@pytest.mark.parametrize("case", ["first-visit", "error", "storage-blocked", "opened"])
def test_22_new_tag_absent_or_cleared_with_glow(session, case):
    if case == "first-visit":
        s = session()
    elif case == "error":
        s = session(seen=OLDEST, status=404, raw="{}")
    elif case == "storage-blocked":
        s = session(seen=OLDEST, storage_throws=True)
    else:
        s = session(seen=OLDEST)
        assert s.ev(TAG)["display"] == "block"
        s.page.click("#whatsNew")                  # same update clears glow and tag
    assert s.state == "none"
    assert s.ev(TAG)["display"] == "none"
    assert s.ev(MARK_SHADOW) == s.ev(HAIRLINE)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_panel_new_pills_stay_visible_and_neutral(session, theme, width):
    """v0.4.1 build note: the tag is .wn-tag; the in-panel .wn-new pills must still show."""
    s = session(seen=OLDEST, theme=theme, width=width, height=844)
    s.page.click("#whatsNew")
    wait_panel_rendered(s)
    pills = s.page.locator("#whatsNewPanel .wn-new")
    assert pills.count() == 2
    for i in range(2):
        assert pills.nth(i).is_visible()
        box = pills.nth(i).bounding_box()
        assert box["width"] > 10 and box["height"] > 8
    muted = s.ev("getComputedStyle(document.documentElement).getPropertyValue('--muted').trim()").lower()
    border = s.ev("getComputedStyle(document.querySelector('#whatsNewPanel .wn-new')).borderTopColor")
    assert _rgb(border)[:3] == _hex(muted)
    assert s.page.locator("#whatsNewPanel .wn-tag").count() == 0


@pytest.mark.parametrize("dsf,expected", [(1, "clay-mark-22.png"), (2, "clay-mark-44.png"), (3, "clay-mark-66.png"), (4, "clay-mark-88.png")])
def test_21_mark_srcset_resolves_per_dpr(session, dsf, expected):
    s = session(dsf=dsf)
    s.page.wait_for_function("document.querySelector('.wn-mark').complete && document.querySelector('.wn-mark').naturalWidth > 0")
    assert s.ev("document.querySelector('.wn-mark').currentSrc").endswith("/static/brand/" + expected)


def test_21_favicon_links_resolve_200(session):
    s = session()
    hrefs = s.ev("[...document.querySelectorAll('link[rel~=icon], link[rel=apple-touch-icon]')].map(l => l.href)")
    assert len(hrefs) == 4
    for href in hrefs:
        r = s.page.request.get(href)
        assert r.status == 200 and r.headers["content-type"].startswith("image/"), href
