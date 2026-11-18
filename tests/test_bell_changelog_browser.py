"""What changed: the header bell, its panel and /changes (Playwright).

Ikenna, 2026-10-23 9:55 PM ET: "the new tag is literally like a ... notification center ... with the change log.
Look at the ... family tree." Pattern from mdvnavy/nwakwesi-family-tree (initBell / renderChangelog).
Skipped when Playwright/Chromium is unavailable.
"""

import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEEN = "dia-changes-seen-v1"
LOG = json.loads((ROOT / "static" / "changelog.json").read_text(encoding="utf-8"))
ENTRIES = LOG["entries"]
NEWEST = ENTRIES[0]["key"]


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


def session(browser, base_url, *, seen=None, extra=None, width=1280, height=800, touch=False, theme="dark", path="/"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, reduced_motion="reduce")
    init = f"try {{ localStorage.setItem('dia-theme', {json.dumps(theme)}); }} catch (e) {{}}\n"
    init += f"if (!sessionStorage.getItem('cl-init')) {{ sessionStorage.setItem('cl-init', '1');"
    init += f" localStorage.removeItem({json.dumps(SEEN)});"
    if seen is not None:
        init += f" localStorage.setItem({json.dumps(SEEN)}, {json.dumps(seen)});"
    for k, v in (extra or {}).items():
        init += f" localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
    init += " }\n"
    ctx.add_init_script(init)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base_url + path)
    if path == "/":
        page.wait_for_function("document.querySelector('#bell')?.dataset.ready === '1'", timeout=8000)
    return ctx, page, errors


def has_dot(page):
    return page.evaluate("document.querySelector('#bell-btn').classList.contains('has-new')")


def panel_open(page):
    page.wait_for_function("document.querySelector('#bell-panel').classList.contains('open') && "
                           "getComputedStyle(document.querySelector('#bell-panel')).visibility === 'visible'", timeout=4000)
    page.wait_for_selector("#bell-panel .dia-bell-list a")


def stored(page):
    return page.evaluate(f"localStorage.getItem({json.dumps(SEEN)})")


def test_changelog_has_entries():
    assert ENTRIES and len({e["key"] for e in ENTRIES}) == len(ENTRIES)


def test_dot_shown_when_unseen(browser, base_url):
    ctx, page, errors = session(browser, base_url)
    try:
        assert has_dot(page)
        assert page.get_attribute("#bell-btn", "aria-label").startswith("What changed, ")
        assert page.get_attribute("#bell-btn", "aria-expanded") == "false"
        dot = page.eval_on_selector("#bell-btn .dia-bell-dot", "d => { const s = getComputedStyle(d); return [s.display, s.width]; }")
        assert dot == ["block", "8px"]
        assert not errors
    finally:
        ctx.close()


def test_old_whats_new_state_cannot_hide_the_dot(browser, base_url):
    # Whatever the earlier What's new indicator stored (and any older key for the bell) must not suppress tonight's list.
    ctx, page, _ = session(browser, base_url, extra={"dia-whats-new-seen": "2026-10-23-find-any-action", "dia-changes-seen": NEWEST})
    try:
        assert has_dot(page)
    finally:
        ctx.close()


def test_no_dot_when_newest_already_seen(browser, base_url):
    ctx, page, _ = session(browser, base_url, seen=NEWEST)
    try:
        assert not has_dot(page)
        assert page.get_attribute("#bell-btn", "aria-label") == "What changed"
    finally:
        ctx.close()


def test_dot_returns_when_a_newer_change_lands(browser, base_url):
    if len(ENTRIES) < 2:
        pytest.skip("needs two entries")
    ctx, page, _ = session(browser, base_url, seen=ENTRIES[1]["key"])
    try:
        assert has_dot(page)
        assert page.get_attribute("#bell-btn", "aria-label") == "What changed, 1 new"
    finally:
        ctx.close()


def test_hover_opens_latest_five_and_clears_dot(browser, base_url):
    ctx, page, errors = session(browser, base_url)
    try:
        page.hover("#bell-btn")
        panel_open(page)
        assert page.get_attribute("#bell-btn", "aria-expanded") == "true"
        items = page.locator("#bell-panel .dia-bell-list > li")
        assert items.count() == min(5, len(ENTRIES))
        assert page.inner_text("#bell-panel .dia-bell-list > li:first-child b") == ENTRIES[0]["title"]
        assert page.locator("#bell-panel .cl-new").count() == min(5, len(ENTRIES))   # never seen: all marked New
        # Glyph's clay NEW tag: the theme's image at 22x8, loaded; the word stays for screen readers
        tag = page.eval_on_selector("#bell-panel .cl-new", """t => { const i = [...t.querySelectorAll('img')].find(x => getComputedStyle(x).display !== 'none');
          return [i.currentSrc.split('/').pop(), i.getBoundingClientRect().width, i.getBoundingClientRect().height, i.complete && i.naturalWidth > 0, t.textContent]; }""")
        assert tag[0].startswith("clay-new-dark-") and tag[1:] == [22, 8, True, "New"]
        assert not has_dot(page)
        assert stored(page) == NEWEST
        page.mouse.move(5, 790)
        page.wait_for_function("!document.querySelector('#bell-panel').classList.contains('open')", timeout=3000)
        page.reload()
        page.wait_for_function("document.querySelector('#bell').dataset.ready === '1'", timeout=8000)
        assert not has_dot(page)   # cleared after open, for good
        assert not errors
    finally:
        ctx.close()


def test_tap_opens_then_second_tap_goes_to_the_full_list(browser, base_url):
    ctx, page, _ = session(browser, base_url, width=390, height=844, touch=True)
    try:
        page.tap("#bell-btn")
        panel_open(page)
        assert page.url.endswith("/")          # the first tap only opens the list
        assert not has_dot(page)
        box = page.locator("#bell-panel").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        page.tap("#bell-btn")
        page.wait_for_url("**/changes")
    finally:
        ctx.close()


def test_keyboard_focus_opens_tab_moves_in_escape_returns(browser, base_url):
    ctx, page, _ = session(browser, base_url)
    try:
        page.focus("#bell-btn")
        panel_open(page)
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('#bell-panel') !== null")
        assert page.evaluate("document.activeElement.getAttribute('href')") == "/changes#" + ENTRIES[0]["key"]
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('#bell-panel').classList.contains('open')", timeout=3000)
        assert page.evaluate("document.activeElement.id") == "bell-btn"
        assert page.get_attribute("#bell-btn", "aria-expanded") == "false"
        # Enter on the bell follows its link to the full list
        page.keyboard.press("Enter")
        page.wait_for_url("**/changes")
    finally:
        ctx.close()


def test_tab_past_last_link_leaves_the_panel(browser, base_url):
    ctx, page, _ = session(browser, base_url)
    try:
        page.focus("#bell-btn")
        panel_open(page)
        page.focus("#bell-panel .dia-bell-all")
        page.keyboard.press("Tab")
        page.wait_for_function("!document.querySelector('#bell-panel').classList.contains('open')", timeout=3000)
        assert page.evaluate("document.activeElement.id") == "paletteTrigger"
    finally:
        ctx.close()


def test_see_all_link_opens_the_full_view(browser, base_url):
    ctx, page, _ = session(browser, base_url)
    try:
        page.hover("#bell-btn")
        panel_open(page)
        link = page.locator("#bell-panel .dia-bell-all")
        assert link.inner_text() == "See all changes"
        assert link.get_attribute("href") == "/changes"
        link.click()
        page.wait_for_url("**/changes")
        page.wait_for_selector("#changes-list .cl-entry h2")
    finally:
        ctx.close()


def test_full_view_lists_every_change_and_marks_seen(browser, base_url):
    ctx, page, errors = session(browser, base_url, path="/changes")
    try:
        page.wait_for_selector("#changes-list .cl-entry h2")
        assert page.title().startswith("What changed")
        assert page.inner_text("h1") == "What changed"
        assert page.locator("#changes-list > li.cl-entry").count() == len(ENTRIES)
        titles = page.eval_on_selector_all("#changes-list .cl-entry h2", "hs => hs.map(h => h.childNodes[0].textContent)")
        assert titles == [e["title"] for e in ENTRIES]
        assert page.locator("#changes-list .cl-new").count() == len(ENTRIES)
        assert page.get_attribute("#changes-list", "aria-busy") == "false"
        assert stored(page) == NEWEST
        assert page.get_attribute(".changes-back", "href") == "/"
        assert not errors
    finally:
        ctx.close()


def test_entry_link_deep_links_into_the_full_view(browser, base_url):
    key = ENTRIES[-1]["key"]
    ctx, page, _ = session(browser, base_url, path="/changes#" + key)
    try:
        page.wait_for_selector("#changes-list .cl-entry h2")
        page.wait_for_function(f"document.activeElement && document.activeElement.id === {json.dumps(key)}", timeout=3000)
    finally:
        ctx.close()


def test_menu_command_opens_the_panel(browser, base_url):
    ctx, page, _ = session(browser, base_url)
    try:
        page.evaluate("window.DiaWhatsNew.open()")
        panel_open(page)
        page.wait_for_function("document.activeElement.closest('#bell-panel') !== null", timeout=3000)
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [1280, 390, 360])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bell_fits_the_band_without_overlap(browser, base_url, width, theme):
    ctx, page, _ = session(browser, base_url, width=width, height=800, theme=theme)
    try:
        r = page.evaluate("""() => {
          const bell = document.querySelector('#bell-btn'), b = bell.getBoundingClientRect();
          const band = document.querySelector('.top-nav').getBoundingClientRect();
          const others = [...document.querySelectorAll('.top-nav button, .top-nav a, #lhClock')]
            .filter(e => e !== bell && e.offsetParent !== null && !e.closest('.nav-dropdown'));
          const hits = others.filter(e => { const o = e.getBoundingClientRect();
            return o.width && !(o.right <= b.left || o.left >= b.right || o.bottom <= b.top || o.top >= b.bottom); });
          const dot = getComputedStyle(bell.querySelector('.dia-bell-dot'));
          return { w: b.width, h: b.height, inBand: b.top >= band.top && b.bottom <= band.bottom && b.left >= 0 && b.right <= innerWidth,
                   hits: hits.map(e => e.id || e.className), scrollW: document.documentElement.scrollWidth,
                   dot: dot.backgroundColor, ring: dot.boxShadow, bg: getComputedStyle(bell).backgroundColor };
        }""")
        assert (r["w"], r["h"]) == (36, 36)
        assert r["inBand"] and not r["hits"], r
        assert r["scrollW"] <= width
        assert "223, 168, 28" not in r["dot"]          # mustard is drag-over only
        assert r["dot"] != "rgba(0, 0, 0, 0)" and "2px" in r["ring"]
    finally:
        ctx.close()


TAG_JS = """() => { const t = document.querySelector('#bell-btn .dia-bell-tag'); const s = getComputedStyle(t);
  const img = [...t.querySelectorAll('img')].find(x => getComputedStyle(x).display !== 'none');
  const r = t.getBoundingClientRect(), b = document.querySelector('#bell-btn svg').getBoundingClientRect(),
        c = document.querySelector('#lhTime').getBoundingClientRect();
  return { display: s.display, src: img ? img.currentSrc : null, loaded: !!img && img.complete && img.naturalWidth > 0,
           w: r.width, h: r.height, centred: Math.abs((r.left + r.right) / 2 - (b.left + b.right) / 2) <= 0.5,
           gap: r.top - b.bottom, clearOfClock: r.left >= c.right, inView: r.bottom <= innerHeight && r.right <= innerWidth }; }"""


@pytest.mark.parametrize("width", [1280, 390, 360])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_clay_new_tag_hangs_under_the_bell_while_unseen(browser, base_url, width, theme):
    # Glyph's clay NEW tag (the #47 asset, unchanged) rides with the dot: 22x8, centred 1px under the icon.
    ctx, page, _ = session(browser, base_url, width=width, height=800 if width > 600 else 844, theme=theme)
    try:
        page.wait_for_function("[...document.querySelectorAll('#bell-btn .dia-bell-tag img')].some(i => i.complete && i.naturalWidth > 0)")
        t = page.evaluate(TAG_JS)
        assert t["display"] == "block" and t["loaded"] and f"/static/brand/clay-new-{theme}-" in t["src"]
        assert (t["w"], t["h"]) == (22, 8) and t["centred"] and abs(t["gap"] - 1) <= 0.5
        assert t["clearOfClock"] and t["inView"]
        assert page.get_attribute("#bell-btn .dia-bell-tag", "aria-hidden") == "true"
        page.hover("#bell-btn")
        panel_open(page)
        assert page.evaluate(TAG_JS)["display"] == "none"      # opening clears the dot and the tag together
    finally:
        ctx.close()


def test_clay_new_tag_absent_when_seen(browser, base_url):
    ctx, page, _ = session(browser, base_url, seen=NEWEST)
    try:
        assert page.evaluate(TAG_JS)["display"] == "none"
    finally:
        ctx.close()
