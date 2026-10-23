"""What's new indicator (spec proto/whats-new-indicator.md v0.3 §10), browser acceptance tests (Playwright).

v0.4.1: silver glow on the mark (--wn-glow-*), plus an aria-hidden NEW tag (.wn-tag) across the mark's bottom edge.
Dia only; the Tia half (test 19 and the Tia legs of 9 and 20-24) ships with Tia's letterhead branch.
Skipped when Playwright/Chromium is unavailable.
"""

import colorsys
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

KEY = "dia-whats-new-seen"
PENDING_TAG = pytest.mark.skip(reason="pending spec v0.4.2: NEW tag styling held (Ikenna 2:14 PM ET; bare red text under the mark)")
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
                 hold_json=False):
        self.base_url = base_url
        self.context = browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=dsf, reduced_motion=reduced_motion,
            forced_colors=forced_colors, color_scheme=theme, user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
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
  const c = getComputedStyle(img), t = getComputedStyle(tag);
  return { style: c.outlineStyle, width: c.outlineWidth, offset: c.outlineOffset, color: c.outlineColor, hl: sys('Highlight'),
           shadow: c.boxShadow, tagDisplay: t.display, tagBg: t.backgroundColor, tagFg: t.color, tagBorder: [t.borderTopStyle, t.borderTopWidth, t.borderTopColor],
           canvas: sys('Canvas'), canvasText: sys('CanvasText') }; }"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_11_24_forced_colors_highlight_outline_and_system_tag(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width, forced_colors="active")
    assert s.state == "new"
    f = s.ev(FORCED)
    assert f["style"] == "solid" and f["width"] == "2px" and f["offset"] == "2px" and f["color"] == f["hl"]
    assert f["shadow"] == "none"
    assert f["tagDisplay"] == "block" and f["tagFg"] == f["canvasText"]   # tag box/border held for v0.4.2
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
    s = session(seen=OLDEST, theme=theme, width=width)
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


@PENDING_TAG
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_23_tag_size_position_and_clearance(session, theme, width):
    """Final numbers come with v0.4.2 (tag sits just under the mark, no pill)."""
    s = session(seen=OLDEST, theme=theme, width=width, height=844)
    g = s.ev(GEOM)
    bl, bt, br, bb = g["btn"]
    il, it, ir, ib = g["img"]
    tl, tt, tr, tb, tw, th = g["tag"]
    assert tt >= ib                                                        # under the mark, not overlapping
    assert tl >= bl and tr <= br and tb <= bb                              # inside the 44x44 button
    assert g["tagGap"] >= 8, g


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_23_cursor_underscore_pixels_visible_with_tag(session, theme):
    """Pixel check: the green underscore still renders above the tag (DPR 4 crop of the mark)."""
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

SILVER = {
    "dark": "rgba(217, 220, 224, 0.8) 0px 0px 0px 1px, rgba(217, 220, 224, 0.42) 0px 0px 10px 1px",
    "light": "rgb(124, 130, 138) 0px 0px 0px 1px, rgba(163, 169, 177, 0.55) 0px 0px 9px 1px",
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
            if reddish and "wn-tag" in classes and pseudo is None and prop == "color":
                tag_red.append(m)
            elif reddish or goldish or (round(r), round(g), round(b)) == (223, 168, 28):
                bad.append((who, pseudo, prop, m))
    assert bad == []
    assert len(tag_red) == 1   # the tag text; exact per-theme reds come with v0.4.2


def _contrast(a, b):
    l1, l2 = sorted([_lum(a), _lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


@PENDING_TAG
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [1280, 390])
def test_22_new_tag_tokens_and_hairline(session, theme, width):
    s = session(seen=OLDEST, theme=theme, width=width)
    t = s.ev(TAG)
    assert t["display"] == "block" and t["hidden"] == "true"
    assert t["color"] == "rgb(255, 77, 106)" and t["bg"] == "rgb(26, 26, 28)"
    assert round(_contrast(_rgb(t["color"]), _rgb(t["bg"])), 2) >= 5.39
    if theme == "dark":
        assert t["shadow"] == "rgba(217, 220, 224, 0.3) 0px 0px 0px 1px"
    else:
        assert t["shadow"] in ("rgba(0, 0, 0, 0) 0px 0px 0px 1px", "none")
    assert t["border"].startswith("none")         # size comes from padding, not a border
    font = s.ev("(() => { const c = getComputedStyle(document.querySelector('#whatsNew .wn-tag')); return [c.fontSize, c.fontWeight, c.textTransform, c.letterSpacing, c.paddingTop, c.paddingLeft, c.borderRadius]; })()")
    assert font[:3] == ["9px", "700", "uppercase"] and abs(float(font[3][:-2]) - 0.36) < 0.01
    assert font[4:] == ["2px", "4px", "3px"]
    assert s.ev("document.querySelector('#whatsNew .wn-tag').textContent") == "New"


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
