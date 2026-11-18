"""Link row separator (fix-link-row-separator, 2026-10-23), updated for the composer v3 first-screen cleanup:
Ikenna (2026-10-23 21:49-22:02 ET) removed the under-prompt link row. "Upload a file" is gone (the tray opens the file
chooser), and "Load sample" / "Learn how to use DIA" moved into the help popover. So no link or "·" separator may be
painted under the bar at any width, and the popover's actions carry no separators either (no dangling "·" anywhere).

Runs the real app in-process and drives it with Playwright (Chromium); skipped when Chromium isn't available."""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402

# Every visible child of the row in order: its text, whether it is a separator, whether it is painted, and its box.
ROW_JS = """
() => [...document.querySelector('.actions.under-prompt').children].filter((el) => !el.hidden).map((el) => {
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  return { text: el.textContent.trim(), sep: el.classList.contains('dot-sep'),
           painted: cs.visibility === 'visible' && cs.display !== 'none' && r.width > 0 && r.height > 0,
           top: r.top, bottom: r.bottom, left: r.left, right: r.right };
})
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


def _page(browser, base_url, width, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 800})
    context.add_init_script(f"try {{ localStorage.setItem('dia-theme', '{theme}'); }} catch (e) {{}}")
    page = context.new_page()
    page.goto(base_url + "/")
    page.wait_for_selector("#helpPopoverBtn")
    page.wait_for_timeout(300)
    return context, page


@pytest.mark.parametrize("width", [1280, 390, 360])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_link_row_or_separator_under_the_bar(browser, base_url, width, theme):
    context, page = _page(browser, base_url, width, theme)
    try:
        painted = [i["text"] for i in page.evaluate(ROW_JS) if i["painted"]]
        assert painted == [], painted
        assert page.evaluate("[...document.querySelectorAll('.dot-sep')].filter(e => e.getBoundingClientRect().width > 0).length") == 0
    finally:
        context.close()


@pytest.mark.parametrize("width", [1280, 390, 360])
def test_help_popover_actions_have_no_dangling_separator(browser, base_url, width):
    context, page = _page(browser, base_url, width)
    try:
        page.click("#helpPopoverBtn")
        page.wait_for_selector("#helpPopover:not([hidden])")
        texts = page.eval_on_selector_all("#helpPopoverActions > *:not([hidden])", "els => els.map(e => e.textContent.trim())")
        assert texts == ["Load sample", "Learn how to use DIA"], texts
        assert "·" not in page.eval_on_selector("#helpPopover", "e => e.innerText")
    finally:
        context.close()
