"""Composer mic (dictation + voice cluster fan) and enter icon: static wiring checks."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")
ICON_DIRS = ["line", "zelda", "pixel", "pixel-dark"]
COMPOSER_ICONS = ["mic", "mic-listening", "mic-disabled", "enter", "conversation", "listen", "listen-stop", "autospeak-on", "autospeak-off"]


def _composer() -> str:
    start = INDEX.index('id="intakeComposer"')
    return INDEX[start:INDEX.index("</textarea>", start)]


def test_mic_is_first_in_composer_and_mirrors_send():
    composer = _composer()
    mic = re.search(r"<button id=\"intakeMic\"[^>]*>", composer).group(0)
    assert composer.index('id="intakeMic"') < composer.index('id="questionnaire"')
    assert 'class="action-btn dictate-btn"' in mic  # same circle as send
    assert 'aria-label="Dictate"' in mic and 'aria-pressed="false"' in mic
    assert 'aria-haspopup="menu"' in mic and 'aria-expanded="false"' in mic and 'aria-controls="voiceFan"' in mic
    assert 'data-icon="mic" data-icon-off="mic" data-icon-on="mic-listening" data-icon-disabled="mic-disabled"' in composer
    assert "has-mic" in INDEX[INDEX.index('id="intakeComposer"') - 60:INDEX.index('id="intakeComposer"')]


def test_send_buttons_show_enter_icon():
    for button in ("runAgent", "runChat"):
        start = INDEX.index(f'id="{button}"')
        markup = INDEX[start:INDEX.index("</button>", start)]
        assert 'data-icon="enter"' in markup
        assert "M5 12h14M12 5l7 7-7 7" not in markup


def test_voice_fan_items_proxy_existing_controls():
    start = INDEX.index('id="voiceFan"')
    fan = INDEX[start:INDEX.index("</div>", start)]
    assert 'role="menu"' in INDEX[start - 60:start + 120]
    for item, icon in [("fanConversation", "conversation"), ("fanListen", "listen"), ("fanAutoSpeak", "autospeak-off")]:
        tag = re.search(rf"<button id=\"{item}\"[^>]*>", fan).group(0)
        assert 'role="menuitemcheckbox"' in tag and "aria-label=" in tag and 'tabindex="-1"' in tag
        assert f'data-icon="{icon}"' in fan
    # One source of truth: the fan clicks the Ask DIA controls and mirrors their aria-pressed.
    for source in ("conversationMode.click()", "listenAgent.click()", "autoSpeak.click()", "MutationObserver"):
        assert source in APP_JS
    for const in ("HOVER_INTENT_MS = 120", "LONG_PRESS_MS = 400"):
        assert const in APP_JS


def test_dictation_uses_shared_helper_for_both_mics():
    assert "function startRecognizer(" in APP_JS and "function attachDictation(" in APP_JS
    assert "attachDictation(micButton, agentMessage" in APP_JS
    assert "attachDictation(intakeMic, questionnaire" in APP_JS
    assert APP_JS.count("new SpeechRecognitionImpl(") == 1


@pytest.mark.parametrize("folder", ICON_DIRS)
@pytest.mark.parametrize("name", COMPOSER_ICONS)
def test_composer_icons_exist_for_every_style(folder, name):
    svg = (ROOT / "static/icons" / folder / f"{name}.svg").read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg") and "<script" not in svg


def test_voice_fan_motion_uses_tokens_and_is_gated():
    gated = CSS[CSS.index("Voice cluster: the composer mic"):]
    block = gated[gated.index("@media (prefers-reduced-motion: no-preference)"):]
    assert "var(--dia-ease-out" in block and "var(--dia-stagger-open" in block and "210ms" in block
    assert "translate(" in block


def test_stateful_icon_slots_follow_button_state():
    assert "function syncStateIcons(" in APP_JS
    assert 'data-icon-on="listen-stop"' in INDEX and 'data-icon-on="autospeak-on"' in INDEX
