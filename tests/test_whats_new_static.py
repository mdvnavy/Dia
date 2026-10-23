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


# Spec v0.5 tokens (Proto's mock CSS, Glyph's 2:33 PM ET glow and clay NEW image).
TOKENS = {
    "dark": {"--wn-target": "44px", "--wn-size": "22px", "--wn-radius": "5px", "--wn-gap": "12px", "--wn-rest-ring": "var(--border)",
             "--wn-fade": "1200ms", "--wn-glow-ring": "rgba(217, 220, 224, 0.90)", "--wn-glow-rgb": "217, 220, 224",
             "--wn-glow-alpha": "0.55", "--wn-glow-blur": "13px", "--wn-glow-spread": "1.3px", "--wn-tag-text-dark": "#FF5C77",
             "--wn-tag-text-light": "#C1102D", "--wn-tag-text": "var(--wn-tag-text-dark)", "--wn-tag-offset-y": "1px",
             "--wn-tag-img-w": "22px", "--wn-tag-img-h": "8px"},
    "light": {"--wn-glow-ring": "rgba(124, 130, 138, 1.0)", "--wn-glow-rgb": "163, 169, 177", "--wn-glow-alpha": "0.72",
              "--wn-glow-blur": "12px", "--wn-glow-spread": "1.3px", "--wn-tag-text": "var(--wn-tag-text-light)"},
}


def test_token_blocks_are_exactly_the_given_values():
    dark, light = _blocks(CSS)[:2]
    assert _tokens(dark) == TOKENS["dark"] and _tokens(light) == TOKENS["light"]
    mock = Path("/workspace/design/2026-10-23_whats-new-indicator_mock/whats-new-indicator.css")
    if mock.exists() and "spec v0.5" in mock.read_text():       # verbatim copy of Proto's v0.5 blocks
        assert _blocks(mock.read_text())[:2] == [dark, light]


def test_tokens_no_pill_and_clay_image_box():
    dark, light = _blocks(CSS)[:2]
    d, l = _tokens(dark), _tokens(light)
    assert d["--wn-gap"] == "12px" and d["--wn-tag-offset-y"] == "1px"
    assert (d["--wn-tag-img-w"], d["--wn-tag-img-h"]) == ("22px", "8px")
    assert not any(k in d or k in l for k in ("--wn-tag-bg", "--wn-tag-hairline", "--wn-tag-pad", "--wn-tag-radius",
                                              "--wn-tag-size", "--wn-tag-weight", "--wn-tag-track"))


def test_mode_offsets_and_new_state_focus_ring():
    flat = re.sub(r"\s+", " ", CSS)
    for media in ("@media (prefers-contrast: more)", "@media (forced-colors: active)"):
        block = flat.split(media, 1)[1].split("} }", 1)[0]
        assert '.wn-btn[data-state="new"] { --wn-tag-offset-y: 2px; }' in block, media
    forced = flat.split("@media (forced-colors: active) { .wn-mark", 1)[1]
    assert '.wn-btn[data-state="new"] .wn-tag-img { display: none !important; }' in forced
    assert '.wn-btn[data-state="new"] .wn-tag-text { display: block; color: CanvasText; font: 700 9px/1' in forced
    assert ".wn-tag-text { display: none; }" in flat
    assert "inset: 4px 4px -2px 4px" in flat
    assert "outline: 2px solid Highlight; outline-offset: 0;" in flat


def _strip_token_blocks(css: str) -> str:
    for block in _blocks(css):
        css = css.replace(block, "")
    return css


def test_no_red_or_mustard_outside_token_definitions():
    body = _strip_token_blocks(CSS) + JS
    assert not re.search(r"#ff4d6a|#ff5c77|#c1102d|255,\s*92,\s*119|193,\s*16,\s*45|#dfa81c", body, re.I)
    assert "wn-glow-rgb" in CSS and "--wn-tag-text:" in CSS     # v0.5: the reds live in Glyph's asset; tokens document them


def test_glow_is_on_the_img_with_no_overlay():
    assert re.search(r'\.wn-btn\[data-state="new"\] \.wn-mark\s*\{[^}]*box-shadow', CSS)
    assert "::after" not in _strip_token_blocks(CSS).split("/* panel")[0]


def test_tag_markup_clay_images_and_aria_hidden():
    tag = re.search(r'<span class="wn-tag" id="whatsNewTag" aria-hidden="true">(.*?)\n            </span>', HTML, re.S).group(1)
    for t in ("light", "dark"):
        b = "/static/brand/clay-new-" + t
        img = re.search(r'<img class="wn-tag-img wn-tag-img--%s"[^>]*>' % t, tag).group(0)
        assert f'src="{b}-22.png"' in img and f'srcset="{b}-22.png 1x, {b}-44.png 2x, {b}-66.png 3x"' in img
        assert 'width="22" height="8"' in img and 'alt=""' in img
        for n in (22, 44, 66):   # Glyph's files, unmodified
            ours, glyph = ROOT / "static" / "brand" / f"clay-new-{t}-{n}.png", Path(f"/workspace/dia-tia/glyph/brand/clay-new-{t}-{n}.png")
            assert ours.exists() and (not glyph.exists() or ours.read_bytes() == glyph.read_bytes())
    assert '<span class="wn-tag-text">New</span>' in tag      # forced-colours text
    assert 'class="wn-new"' not in HTML                     # .wn-new stays the in-panel pill only
    main = re.search(r'\.wn-btn\[data-state="new"\] \.wn-tag\s*\{([^}]*)\}', CSS).group(1)
    assert not re.search(r"(?<![-\w])border(-width)?\s*:", main)
    assert not re.search(r"background(-color)?\s*:", main) and "padding: 0" in main   # no pill (transparent by default)
    assert "transform: translateX(-50%)" in main


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
