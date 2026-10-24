"""Composer layout (plus | input | mic | enter), voice-options notch fan, plus menu and Donate: static wiring checks."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")
COMPOSER_CSS = (ROOT / "static/composer.css").read_text(encoding="utf-8")
ICONS_CSS = (ROOT / "static/composer-icons.css").read_text(encoding="utf-8")
ICON_DIRS = ["line", "zelda", "pixel", "pixel-dark"]
COMPOSER_ICONS = ["mic", "mic-listening", "mic-disabled", "enter", "conversation", "listen", "listen-stop",
                  "autospeak-on", "autospeak-off", "plus", "plus-open", "notch", "notch-open", "menu-attach", "menu-sample",
                  "menu-ask", "menu-icon-style", "menu-theme", "menu-help", "menu-commands", "menu-donate"]
COMPOSERS = [("intake", "intakeComposer", "questionnaire", "intakeMic", "runAgent"),
             ("agent", "agentComposer", "agentMessage", "micButton", "runChat")]


def _block(element_id: str) -> str:
    start = INDEX.rindex("<div", 0, INDEX.index(f'id="{element_id}"'))
    depth, i = 0, start
    for match in re.finditer(r"<(/?)div\b", INDEX[start:]):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return INDEX[start:start + match.end()]
    raise AssertionError(element_id)


def _tag(markup: str, element_id: str) -> str:
    return re.search(rf"<[a-z]+ [^>]*id=\"{element_id}\"[^>]*>", markup).group(0)


def test_ids_are_unique():
    dupes = [i for i, n in Counter(re.findall(r'\sid="([^"]+)"', INDEX)).items() if n > 1]
    assert dupes == []
    for old in ('id="voiceFan"', 'id="voiceCluster"', 'id="fanConversation"'):
        assert old not in INDEX


@pytest.mark.parametrize("prefix,composer_id,textarea,mic,run", COMPOSERS)
def test_composer_order_plus_input_enter_mic(prefix, composer_id, textarea, mic, run):
    bar = _block(composer_id)
    assert 'data-empty="true"' in _tag(INDEX, composer_id) and "has-cmp" in _tag(INDEX, composer_id)
    # Glyph review #4 (WCAG 2.4.3): Run comes before the mic in the DOM so Tab follows the visual order.
    order = [bar.index(f'id="{x}"') for x in (f"{prefix}PlusButton", textarea, run, mic)]
    assert order == sorted(order)
    mic_tag = _tag(bar, mic)
    assert "cmp-circle cmp-mic" in mic_tag and 'aria-pressed="false"' in mic_tag
    assert 'aria-label="Start voice input"' in mic_tag and 'data-label-on="Stop voice input"' in mic_tag
    assert "aria-haspopup" not in mic_tag and "title=" not in mic_tag  # the mic only dictates; custom tooltip only
    assert f'id="{prefix}MicTip"' in bar and 'role="tooltip"' in bar
    run_tag = _tag(bar, run)
    assert "cmp-circle cmp-enter" in run_tag and " hidden" in run_tag and "disabled" not in run_tag  # hidden when empty
    assert bar.index('class="cmp-run-slot"') < bar.index(f'id="{run}"')  # slot collapses while empty (mic in the corner)


@pytest.mark.parametrize("prefix,composer_id,textarea,mic,run", COMPOSERS)
def test_voice_notch_fan(prefix, composer_id, textarea, mic, run):
    dock = _block(composer_id)
    # Floating notch (landing polish): a handle bar above the bar replaces the 36x12 notch/notch-open tab icons.
    assert 'class="cmp-notch cmp-notch--float"' in _tag(dock, f"{prefix}VoiceNotch")
    assert 'class="notch-handle"' in dock and 'data-icon="notch"' not in dock
    notch = _tag(dock, f"{prefix}VoiceNotch")
    assert 'aria-haspopup="menu"' in notch and 'aria-expanded="false"' in notch
    assert f'aria-controls="{prefix}VoiceFan"' in notch
    assert 'role="menu"' in _tag(dock, f"{prefix}VoiceFan")
    for item, role, icon, d in [("Conversation", "menuitemcheckbox", "conversation", 1), ("Listen", "menuitem", "listen", 0),
                                ("AutoSpeak", "menuitemcheckbox", "autospeak-off", 1)]:
        tag = _tag(dock, f"{prefix}Fan{item}")
        assert f"--d:{d}" in tag  # centre-out stagger: Listen first
        assert f'role="{role}"' in tag and 'tabindex="-1"' in tag and "aria-label=" in tag
        assert f'data-icon="{icon}"' in dock


@pytest.mark.parametrize("prefix", ["intake", "agent"])
def test_plus_button_and_menu(prefix):
    wrap = _block(f"{prefix}Composer" if prefix == "intake" else "agentComposer")
    button = _tag(wrap, f"{prefix}PlusButton")
    assert "cmp-circle cmp-plus" in button and 'aria-haspopup="menu"' in button and 'aria-expanded="false"' in button
    assert 'aria-label="Actions"' in button
    assert f'aria-controls="{prefix}PlusMenu"' in button and 'data-icon="plus"' in wrap and 'data-icon="plus-open"' in wrap
    menu = _tag(wrap, f"{prefix}PlusMenu")
    assert 'role="menu"' in menu and 'aria-label="Actions"' in menu
    # Glyph's CSS opens it with `.cmp-plus[aria-expanded="true"] + .plus-menu`, so the menu must follow the button.
    assert re.search(rf'id="{prefix}PlusButton"[\s\S]*?</button>\s*<div class="plus-menu"', wrap)


def test_plus_menu_reuses_the_command_registry():
    layout = APP_JS[APP_JS.index("const PLUS_GROUPS"):APP_JS.index("const ICON_STYLE_OPTIONS")]
    for group in ("Add", "Go to", "View", "Help", "Support", "Commands"):
        assert f'label: "{group}"' in layout
    for command_id in ("upload", "load-sample", "cabinet", "ask", "theme", "help", "donate"):
        assert f'id: "{command_id}"' in layout and f'id: "{command_id}"' in APP_JS[APP_JS.index("const commands = ["):]
    assert layout.index('"Support"') < layout.index('"Commands"')  # Donate sits above All commands
    assert '"menuitemradio"' in APP_JS and 'hideIn: "agent"' in layout
    assert "runCommand(command)" in APP_JS and 'text: "All commands"' in layout and "openPalette()" in layout
    assert 'createPlusMenu("intake")' in APP_JS and 'createPlusMenu("agent")' in APP_JS


def test_one_popover_at_a_time():
    assert "const Popovers" in APP_JS and "Popovers.closeOthers(api)" in APP_JS
    assert 'Popovers.closeOthers("cabinet")' in APP_JS
    assert APP_JS.count("Popovers.register(api)") == 2


def test_voice_fan_behaviour_and_timing_tokens():
    fan = APP_JS[APP_JS.index("function createVoiceFan("):APP_JS.index("const VoiceFans")]
    for needle in ('"--dia-delay-intent", 120', '"--dia-delay-grace", 200', '"ArrowUp"', '"Escape"', "pointerenter",
                   "pointerleave", 'pointerType === "touch"', "MutationObserver", 'setAttribute("aria-disabled", "true")',
                   "conversationMode", "listenAgent.click()", "autoSpeak.click()"):
        assert needle in fan
    assert 'createVoiceFan("intake"' in APP_JS and 'createVoiceFan("agent"' in APP_JS
    assert "VoiceCluster" not in APP_JS and "LONG_PRESS_MS" not in APP_JS


def test_run_shows_only_with_text():
    assert "function watchComposer(" in APP_JS and "bar.dataset.empty" in APP_JS
    assert "watchComposer(questionnaire)" in APP_JS and "watchComposer(agentMessage)" in APP_JS
    assert "run.hidden = empty" in APP_JS
    assert ".cmp-enter:not([hidden]) { animation: cmp-run-in var(--dia-dur-run-in)" in ICONS_CSS
    assert "--dia-dur-run-in: 180ms" in ICONS_CSS
    assert "questionnaire.value.trim()" in APP_JS and "agentMessage.value.trim()" in APP_JS


def test_dictation_shared_helper_and_unsupported_state():
    assert "function startRecognizer(" in APP_JS and "function attachDictation(" in APP_JS
    assert "attachDictation(micButton, agentMessage" in APP_JS and "attachDictation(intakeMic, questionnaire" in APP_JS
    assert APP_JS.count("new SpeechRecognitionImpl(") == 1
    helper = APP_JS[APP_JS.index("function attachDictation("):]
    assert 'button.setAttribute("aria-disabled", "true")' in helper and "aria-describedby" in helper
    assert "button.title =" not in helper
    assert 'event.code !== "Space"' in APP_JS  # Cmd/Ctrl+Shift+Space


def test_green_pressed_mic_is_gone():
    assert ".mic-btn" not in CSS and "Voice cluster" not in CSS


def test_glyph_composer_css_is_loaded_last_and_served():
    links = re.findall(r'href="/static/([a-z-]+\.css)"', INDEX)
    assert links[-2:] == ["composer-icons.css", "composer.css"]
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"/static/composer-icons.css"' in app and '"/static/composer.css"' in app
    assert "@keyframes" in ICONS_CSS and "prefers-reduced-motion" in ICONS_CSS
    glyph = Path("/workspace/dia-tia/glyph/composer-icons.css")
    if glyph.exists():
        synced = glyph.read_text(encoding="utf-8").replace("(Ikenna's Stripe link)", "(DIA_DONATE_URL)")
        assert synced in ICONS_CSS  # synced verbatim (bar the Donate href note)
    assert "stripe" not in ICONS_CSS.lower()


@pytest.mark.parametrize("folder", ICON_DIRS)
@pytest.mark.parametrize("name", COMPOSER_ICONS)
def test_composer_icons_exist_for_every_style(folder, name):
    svg = (ROOT / "static/icons" / folder / f"{name}.svg").read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg") and "<script" not in svg


def test_stateful_icon_slots_follow_button_state():
    assert "function syncStateIcons(" in APP_JS and "button.dataset.speaking" in APP_JS
    assert 'data-icon-on="listen-stop"' in INDEX and 'data-icon-on="autospeak-on"' in INDEX


def test_toast_sits_top_centre_and_slides_down():
    toast = CSS[CSS.index(".toast {"):CSS.index(".toast[hidden]")]
    assert "top: calc(64px + env(safe-area-inset-top" in toast and "bottom: auto" in toast
    assert "translateY(-12px)" in COMPOSER_CSS and "dia-toast-fade-only" in COMPOSER_CSS


def test_donate_is_config_driven_and_hidden_when_unset():
    nav = _tag(INDEX, "navDonate")
    assert "hidden" in nav and 'target="_blank"' in nav and 'rel="noopener noreferrer"' in nav
    assert 'href="#"' in nav  # no URL in markup: it comes from DIA_DONATE_URL
    assert 'fetch("/api/config")' in APP_JS and 'window.open(donateUrl, "_blank", "noopener,noreferrer")' in APP_JS
    assert "enabled: () => Boolean(donateUrl)" in APP_JS
    assert 'el.rel = "noopener noreferrer"' in APP_JS and "(opens in a new tab)" in APP_JS and "pm-ext" in APP_JS
    assert 'text: "Donate", icon: "menu-donate"' in APP_JS
    assert 'id: "donate-interac", text: "Donate by Interac e-Transfer", icon: "menu-donate" }' in APP_JS
    assert 'const INTERAC_EMAIL = "payments@bashsystems.net"' in APP_JS
    assert "Copied ${INTERAC_EMAIL}. Send an Interac e-Transfer from your bank" in APP_JS
    assert 'data-command="donate-interac"' in INDEX and 'id: "donate-interac"' in APP_JS
