"""Static checks for the Tia-style shell (no browser needed).

Covers the accessibility fixes from the PR #32 review: one shortcut label in
the help tip, a labelled palette combobox, the decorative BASH mark kept out of
the heading's name, >= 3:1 focus/selection colours, and motion gated behind
prefers-reduced-motion.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _theme_vars(selector: str) -> dict[str, str]:
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", block))


def _resolve(theme: dict[str, str], name: str) -> str:
    value = theme[name].strip()
    while value.startswith("var("):
        value = theme[value[6:-1].strip()].strip()
    return value


def test_help_tip_shows_a_single_shortcut_label():
    tip = re.search(r'<p class="help-tip">(.*?)</p>', HTML, re.S).group(1)
    assert tip.count("⌘K") == 1
    assert "Ctrl K" not in tip  # app.js swaps ⌘K for "Ctrl K" off Mac


def test_palette_input_is_labelled_and_starts_collapsed():
    tag = re.search(r'<input id="paletteInput"[^>]*>', HTML).group(0)
    assert 'aria-label="Search commands"' in tag
    assert 'aria-expanded="false"' in tag


def test_bash_mark_has_empty_alt_text():
    assert re.search(r"content:\s*'BASH'\s*/\s*''", CSS)


def test_focus_ring_meets_non_text_contrast_in_both_themes():
    dark = _theme_vars(":root")
    light = {**dark, **_theme_vars('html[data-theme="light"]')}
    assert re.search(r":focus-visible\s*\{\s*outline:\s*2px solid var\(--focus-ring\)", CSS)
    for theme in (dark, light):
        ring = _hex(_resolve(theme, "focus-ring"))
        for surface in ("bg", "surface", "raised"):
            ratio = _contrast(ring, _hex(_resolve(theme, surface)))
            assert ratio >= 3.0, f"focus ring {ratio:.2f}:1 on --{surface}"


def test_selection_indicators_use_the_focus_ring_colour():
    assert re.search(r'\.palette-item\[aria-selected="true"\][^}]*inset 0 0 0 1px var\(--focus-ring\)', CSS)
    assert re.search(r'html\[data-theme="light"\] \.tab\.active[^}]*border-color: var\(--focus-ring\)', CSS)


def _outside_motion_safe_blocks(css: str) -> str:
    """Return the CSS with every prefers-reduced-motion: no-preference block removed."""
    out, i = [], 0
    marker = "@media (prefers-reduced-motion: no-preference)"
    while True:
        start = css.find(marker, i)
        if start == -1:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:start])
        depth, j = 0, css.index("{", start)
        while True:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1


def test_motion_is_gated_behind_reduced_motion_preference():
    ungated = _outside_motion_safe_blocks(CSS)
    assert not re.findall(r"animation:\s*[^;]+;", ungated)
    for transition in re.findall(r"transition:\s*([^;]+);", ungated):
        # Colour/background fades are fine; movement and size changes are not.
        assert not re.search(r"\b(all|transform|filter|font-size)\b", transition), transition
    assert "scale(1.08)" not in ungated
    assert "translateY(-2px)" not in ungated
