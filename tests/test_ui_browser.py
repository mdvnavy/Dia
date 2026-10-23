"""Browser tests for the command palette and Tia-style shell (Playwright).

Skipped automatically when Playwright or a Chromium build is not installed.
Set DIA_CHROME to a Chrome/Chromium binary to use a system browser.
"""

import os
import threading
from http.server import ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from app import ClientDiscoveryHandler  # noqa: E402


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
def page(browser, base_url):
    # A non-Mac UA so the shortcut is Ctrl+K and the label reads "Ctrl K".
    context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
    p = context.new_page()
    errors = []
    p.on("pageerror", lambda error: errors.append(str(error)))
    p.goto(base_url + "/")
    p.wait_for_function("document.querySelector('#agentStatus').textContent !== 'checking...'")
    yield p
    assert errors == [], errors
    context.close()


def active_id(p):
    return p.evaluate("document.activeElement && document.activeElement.id")


def option_labels(p):
    return p.eval_on_selector_all("#paletteList [role=option] span", "els => els.map(e => e.textContent)")


def test_palette_keyboard_filter_run_and_focus_return(page):
    page.focus("#loadSample")
    page.keyboard.press("Control+k")
    assert page.evaluate("commandPalette.open")
    assert active_id(page) == "paletteInput"
    assert page.get_attribute("#paletteInput", "aria-expanded") == "true"

    # Unavailable commands (exports before a run) are hidden.
    labels = option_labels(page)
    assert "Copy all outputs" not in labels
    assert labels[0] == "New intake"

    # Arrow keys move the active option, wrapping at both ends.
    page.keyboard.press("ArrowDown")
    assert page.get_attribute("#paletteInput", "aria-activedescendant") == "cmd-load-sample"
    page.keyboard.press("End")
    last = page.get_attribute("#paletteInput", "aria-activedescendant")
    page.keyboard.press("ArrowDown")
    assert page.get_attribute("#paletteInput", "aria-activedescendant") == "cmd-new-intake"
    page.keyboard.press("ArrowUp")
    assert page.get_attribute("#paletteInput", "aria-activedescendant") == last

    # Filtering narrows the list and resets the active option.
    page.keyboard.type("dark mode")
    assert option_labels(page) == ["Toggle light / dark mode"]
    page.keyboard.type("zzz")
    assert option_labels(page) == []
    assert page.get_attribute("#paletteInput", "aria-expanded") == "false"
    page.keyboard.press("Escape")
    assert not page.evaluate("commandPalette.open")
    assert page.get_attribute("#paletteInput", "aria-expanded") == "false"
    assert active_id(page) == "loadSample"  # focus returns where it was

    # Enter runs the active command.
    before = page.evaluate("document.documentElement.dataset.theme")
    page.keyboard.press("Control+k")
    page.keyboard.type("dark mode")
    page.keyboard.press("Enter")
    assert not page.evaluate("commandPalette.open")
    assert page.evaluate("document.documentElement.dataset.theme") != before

    # Ctrl+K toggles the palette closed again, also returning focus.
    page.focus("#themeToggle")
    page.keyboard.press("Control+k")
    page.keyboard.press("Control+k")
    assert not page.evaluate("commandPalette.open")
    assert active_id(page) == "themeToggle"


def test_palette_runs_intake_and_new_intake_resets_to_landing(page):
    page.click("#loadSample")
    page.wait_for_function("document.querySelector('#questionnaire').value.length > 0")
    page.keyboard.press("Control+k")
    page.keyboard.type("run intake")
    page.keyboard.press("Enter")
    page.wait_for_function("document.body.dataset.stage === 'results'")
    assert page.evaluate("copyAll.disabled") is False
    assert page.inner_text("#tier") != "-"

    page.keyboard.press("Control+k")
    page.keyboard.type("new intake")
    page.keyboard.press("Enter")
    assert page.evaluate("document.body.dataset.stage") == "landing"
    # Letterhead band (Glyph/Ikenna): the visible crumb is the stage only; text-transform makes inner_text upper-case.
    assert page.text_content("#breadcrumbStage") == "Questionnaire"
    assert page.inner_text("#tier") == "-"
    assert page.inner_text("#score") == "Score pending"
    assert page.evaluate("document.querySelectorAll('#issues li').length") == 0
    assert page.evaluate("[copyAll, downloadMarkdown, downloadJson, shareX].every(b => b.disabled)")
    assert page.input_value("#questionnaire") == ""
    assert active_id(page) == "questionnaire"
    page.keyboard.press("Control+k")
    assert "Copy all outputs" not in option_labels(page)
    page.keyboard.press("Escape")


def test_help_tip_shows_one_platform_label(page):
    assert page.eval_on_selector_all(
        ".help-tip kbd", "els => els.map(e => e.textContent)"
    ) == ["Ctrl K", "/"]


def test_nav_menu_keyboard_home_end_and_tab_out(page):
    page.focus("#navBrandMenu")
    page.keyboard.press("Enter")
    assert page.get_attribute("#navBrandMenu", "aria-expanded") == "true"
    items = page.eval_on_selector_all(".dropdown-item", "els => els.length")
    page.keyboard.press("End")
    assert page.evaluate(f"document.activeElement === document.querySelectorAll('.dropdown-item')[{items - 1}]")
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement === document.querySelector('.dropdown-item')")
    for _ in range(items):
        page.keyboard.press("Tab")
    assert page.get_attribute("#navBrandMenu", "aria-expanded") == "false"
    assert not page.evaluate("navDropdown.classList.contains('show')")


def test_heading_name_excludes_bash_mark(page):
    snapshot = page.locator("h1").aria_snapshot()
    assert "BASH" not in snapshot
    assert "Discovery Intake Agent" in snapshot


def test_reduced_motion_disables_palette_animation(browser, base_url):
    context = browser.new_context(reduced_motion="reduce")
    p = context.new_page()
    p.goto(base_url + "/")
    p.keyboard.press("Control+k")
    assert p.evaluate("getComputedStyle(commandPalette).animationName") == "none"
    assert p.evaluate("getComputedStyle(document.querySelector('.prompt-bar')).transitionDuration") == "0s"
    context.close()


def test_light_focus_ring_uses_dark_silver(page):
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    page.focus("#loadSample")
    page.keyboard.press("Tab")  # keyboard focus so :focus-visible applies
    colour = page.evaluate("getComputedStyle(document.activeElement).outlineColor")
    assert colour == "rgb(95, 102, 112)"
