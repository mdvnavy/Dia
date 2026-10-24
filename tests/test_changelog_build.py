"""scripts/build-changelog.py: the What changed list is built from git history, written for visitors."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_changelog", ROOT / "scripts" / "build-changelog.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)
LOG = json.loads((ROOT / "static" / "changelog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", ["Moved to Gemini", "uses Vertex now", "Glyph's fixes", "the agent replies",
                                  "Claude wrote it", "GPT-5", "OpenAI", "an LLM", "Gate merged it", "ask the bot"])
def test_names_are_banned(text):
    with pytest.raises(SystemExit):
        bc.check_text(text, "test")


@pytest.mark.parametrize("text", ["Sign in with Google", "the gate on Ask DIA", "Bash Systems & Services", "Dia"])
def test_plain_words_pass(text):
    bc.check_text(text, "test")


def test_every_summary_line_is_clean():
    for line in [*bc.SUMMARY.values(), *bc.UPCOMING.values()]:
        if line:
            title, text = line
            bc.check_text(title + " " + text, "SUMMARY")
            assert title and text.endswith(".")


def test_json_matches_summary_and_is_newest_first():
    entries = LOG["entries"]
    assert entries
    for e in entries:
        assert set(e) == {"key", "pr", "date", "title", "text"}
        assert e["key"] == f"pr-{e['pr']}"
        assert (e["title"], e["text"]) == bc.SUMMARY[e["pr"]]
        assert e["pr"] >= bc.FIRST_PR
    dates = [e["date"] for e in entries]
    assert dates == sorted(dates, reverse=True)


def test_tonights_changes_are_listed():
    listed = {e["pr"] for e in LOG["entries"]}
    assert {45, 46, 47, 49} <= listed


def test_user_facing_pr_without_a_line_fails(monkeypatch):
    monkeypatch.setattr(bc, "merged_prs", lambda: [{"pr": 999, "date": "2026-10-24", "files": ["static/app.js"]}])
    with pytest.raises(SystemExit, match="#999"):
        bc.build()
    monkeypatch.setattr(bc, "merged_prs", lambda: [{"pr": 999, "date": "2026-10-24", "files": ["tests/x.py"]}])
    assert bc.build()["entries"] == []


def test_ci_runs_the_check():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/build-changelog.py --check" in ci
