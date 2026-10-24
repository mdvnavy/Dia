"""Link row separator (fix-link-row-separator, 2026-10-23): at 390 and 360 wide the under-prompt link row wraps
"Learn how to use DIA" onto its own line. No "·" may be left hanging after "Load sample" (or lead the second line);
the separator between links that share a line stays, and wide screens keep the one-line row with both separators.

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


def _row(browser, base_url, width, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 800})
    context.add_init_script(f"try {{ localStorage.setItem('dia-theme', '{theme}'); }} catch (e) {{}}")
    page = context.new_page()
    page.goto(base_url + "/")
    page.wait_for_selector(".actions.under-prompt #loadSample")
    page.wait_for_timeout(300)  # let the ResizeObserver / fonts.ready refit settle
    return context, page


def _lines(items):
    """Group painted items into visual lines by vertical overlap."""
    lines = []
    for item in [i for i in items if i["painted"]]:
        for line in lines:
            if item["top"] < line[0]["bottom"] and item["bottom"] > line[0]["top"]:
                line.append(item)
                break
        else:
            lines.append([item])
    return lines


@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_wrapped_link_row_has_no_dangling_separator(browser, base_url, width, theme):
    context, page = _row(browser, base_url, width, theme)
    try:
        lines = _lines(page.evaluate(ROW_JS))
        assert len(lines) == 2, [[i["text"] for i in line] for line in lines]
        assert [i["text"] for i in lines[1]] == ["Learn how to use DIA"]
        assert [i["text"] for i in lines[0]] == ["Upload a file", "·", "Load sample"]
        for line in lines:
            assert not line[0]["sep"] and not line[-1]["sep"], [i["text"] for i in line]
        # both lines stay centred in the row once the dangling "·" is gone
        row = page.evaluate("() => { const r = document.querySelector('.actions.under-prompt').getBoundingClientRect(); return {l: r.left, r: r.right}; }")
        for line in lines:
            assert abs((line[0]["left"] - row["l"]) - (row["r"] - line[-1]["right"])) <= 2
        # the break adds no extra height: one row gap (8px) between the lines, as before the fix
        assert 6 <= lines[1][0]["top"] - lines[0][0]["bottom"] <= 12
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 360])
def test_resizing_back_to_wide_restores_both_separators(browser, base_url, width):
    context, page = _row(browser, base_url, width)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        lines = _lines(page.evaluate(ROW_JS))
        assert [[i["text"] for i in line] for line in lines] == [["Upload a file", "·", "Load sample", "·", "Learn how to use DIA"]]
        page.set_viewport_size({"width": width, "height": 844})
        page.wait_for_timeout(300)
        lines = _lines(page.evaluate(ROW_JS))
        assert [[i["text"] for i in line] for line in lines] == [["Upload a file", "·", "Load sample"], ["Learn how to use DIA"]]
    finally:
        context.close()


def test_wide_link_row_keeps_both_separators(browser, base_url):
    context, page = _row(browser, base_url, 1280)
    try:
        lines = _lines(page.evaluate(ROW_JS))
        assert [[i["text"] for i in line] for line in lines] == [["Upload a file", "·", "Load sample", "·", "Learn how to use DIA"]]
    finally:
        context.close()
