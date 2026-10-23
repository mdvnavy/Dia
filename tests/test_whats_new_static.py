"""Static checks for the Dia "What's new" indicator (spec v0.4.1): tokens, markup, colour rules, release-notes CI."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_whats_new as cw  # noqa: E402

CSS = (ROOT / "static" / "whats-new.css").read_text()
JS = (ROOT / "static" / "whats-new.js").read_text()
HTML = (ROOT / "templates" / "index.html").read_text()
NOTES = json.loads((ROOT / "static" / "whats-new.json").read_text())
MOCK = Path("/workspace/design/2026-10-23_whats-new-indicator_mock/whats-new-indicator.css")


def _blocks(css: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r'^(?::root|html\[data-theme="light"\]) \{.*?^\}', css, re.S | re.M)]


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--wn-[\w-]+):\s*([^;]+);", block))


@pytest.mark.skipif(not MOCK.exists(), reason="Glyph mock folder not on this machine")
def test_non_tag_tokens_match_glyph_mock():
    """Every --wn-* token except the held NEW-tag ones matches Glyph's CSS exactly (tag tokens: pending v0.4.2)."""
    for ours, theirs in zip(_blocks(CSS)[:2], _blocks(MOCK.read_text())[:2]):
        a = {k: v for k, v in _tokens(ours).items() if not k.startswith("--wn-tag")}
        b = {k: v for k, v in _tokens(theirs).items() if not k.startswith("--wn-tag")}
        assert a == b


def test_tag_held_tokens_shape():
    dark, light = _blocks(CSS)[:2]
    d, l = _tokens(dark), _tokens(light)
    assert d["--wn-gap"] == "12px"
    for k in ("--wn-tag-text-dark", "--wn-tag-text-light", "--wn-tag-offset-y"):
        assert k in d, k
    assert d["--wn-tag-text"] == "var(--wn-tag-text-dark)" and l["--wn-tag-text"] == "var(--wn-tag-text-light)"
    assert not any(k in d or k in l for k in ("--wn-tag-bg", "--wn-tag-hairline", "--wn-tag-pad", "--wn-tag-radius"))


def _strip_token_blocks(css: str) -> str:
    for block in _blocks(css):
        css = css.replace(block, "")
    return css


def test_no_red_or_mustard_outside_token_definitions():
    body = _strip_token_blocks(CSS) + JS
    assert not re.search(r"#ff4d6a|255,\s*77,\s*106|#dfa81c", body, re.I)
    assert "wn-glow-rgb" in CSS and "var(--wn-tag-text)" in CSS


def test_glow_is_on_the_img_with_no_overlay():
    assert re.search(r'\.wn-btn\[data-state="new"\] \.wn-mark\s*\{[^}]*box-shadow', CSS)
    assert "::after" not in _strip_token_blocks(CSS).split("/* panel")[0]


def test_tag_markup_own_class_and_aria_hidden():
    assert '<span class="wn-tag" id="whatsNewTag" aria-hidden="true">New</span>' in HTML
    assert 'class="wn-new"' not in HTML                     # .wn-new stays the in-panel pill only
    main = re.search(r'\.wn-btn\[data-state="new"\] \.wn-tag\s*\{([^}]*)\}', CSS).group(1)
    assert not re.search(r"(?<![-\w])border(-width)?\s*:", main)
    assert "background: none" in main and "padding: 0" in main         # bare text, no pill (held, v0.4.2)


def test_real_notes_file_passes_checker():
    assert cw.validate(NOTES) == []
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "check_whats_new.py")]).returncode == 0


def _live(**over):
    note = {"id": "2026-10-23-x", "date": "2026-10-23", "app": "dia", "title": "A title", "body": "A body.",
            "live_flag": True, "source": "CHANGELOG 2026-10-23", "approved_by": "Ikenna", "approved_at": "2026-10-23"}
    note.update(over)
    return {"version": 1, "notes": [note]}


def test_checker_live_note_ok():
    assert cw.validate(_live()) == []


def test_checker_duplicate_ids():
    data = _live()
    data["notes"].append(copy.deepcopy(data["notes"][0]))
    assert any("duplicate" in e for e in cw.validate(data))


@pytest.mark.parametrize("field,size", [("title", 71), ("body", 321)])
def test_checker_over_length(field, size):
    assert any(field in e for e in cw.validate(_live(**{field: "a" * size})))


@pytest.mark.parametrize("word", ["Jules", "Bash", "coming soon"])
def test_checker_banned_words(word):
    assert any("banned" in e for e in cw.validate(_live(body=f"Now with {word} inside.")))


def test_checker_bash_systems_allowed():
    assert cw.validate(_live(body="Works for Bash Systems & Services.")) == []


def test_checker_exclamation_source_and_approval():
    errs = cw.validate(_live(title="Wow!", source=None, approved_by=None))
    assert any("exclamation" in e for e in errs) and any("source" in e for e in errs) and any("approved_by" in e for e in errs)


def test_checker_draft_notes_skip_live_rules():
    assert cw.validate(_live(live_flag=False, body="Jules coming soon!", source=None, approved_by=None)) == []


def test_checker_jules_pr_fails_humans_pass():
    assert cw.check_pr("google-labs-jules[bot]", ["static/whats-new.json"])
    assert cw.check_pr("google-labs-jules[bot]", ["app.py"]) == []
    assert cw.check_pr("mdvnavy", ["static/whats-new.json"]) == []
