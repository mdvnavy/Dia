#!/usr/bin/env python3
"""Build Dia's "What changed" list from this repo's git history.

Writes static/changelog.json, read by the bell in the header and by the /changes page. Newest first, one entry per
merged pull request from FIRST_PR on. A merged pull request is a commit whose subject ends "(#N)" (squash merge) or
starts "Merge pull request #N" (merge commit). Pattern copied from the family tree's scripts/build-changelog.py.

Every line a visitor reads comes from SUMMARY, keyed by pull request number. None leaves a pull request out (tests,
CI, docs). A pull request that changed what visitors see (templates/ or static/) and has no SUMMARY key fails the
build, so nothing ships without a plain-language line. Lines may never name an agent, a model or a provider.
Nothing here needs sign-off: once a line is in SUMMARY and its pull request is merged, it shows.

Usage:
  python scripts/build-changelog.py                        write static/changelog.json from merged history
  python scripts/build-changelog.py --pr 50                also list the current branch as #50 (dated today, ET)
  python scripts/build-changelog.py --check [--pr N]       exit 1 if static/changelog.json is out of date (CI)

On a pull request branch, run it with --pr <its number> and commit the result; once squash-merged, the history
build on main gives the same file. CI runs --check with --pr on pull requests and plain --check on main.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "static" / "changelog.json"
OWN_FILES = {"static/changelog.json"}
USER_FACING = ("templates/", "static/")
FIRST_PR = 45            # the list starts with the 23 October 2026 changes; older history is not back-filled
ET = ZoneInfo("America/New_York")

SQUASH_RE = re.compile(r"\(#(\d+)\)\s*$")
MERGE_RE = re.compile(r"^Merge pull request #(\d+)\b")

# No agent, model or provider names in anything a visitor reads.
IDENTITY_RE = re.compile(
    r"\b(codex|claude|gemini|vertex|antigravity|copilot|jules|opus|sonnet|haiku|gpt\w*|openai|anthropic|grok|xai|"
    r"llms?|agents?|bots?|ai model)\b", re.I)
FLEET_RE = re.compile(r"\b(Glyph|Gate|Forge|Proto|Pulse|Tia)\b")   # the build team's own names (case-sensitive)

# One entry per pull request, in a visitor's words: (title, text). None leaves it out.
SUMMARY: dict[int, tuple[str, str] | None] = {
    46: ("Easier to use on phones and with a keyboard",
         "The status line fits on phones, and the voice panel no longer covers Collapse intake. The voice buttons and "
         "the close button on the inputs card are bigger, so they are easier to tap. Tab now reaches Run before the "
         "mic, and the focus outline in dark mode is grey instead of gold."),
    47: ("A list of what changed",
         "Dia now keeps a list of recent changes, so you can see what is new in one place."),
    45: ("A slimmer prompt bar",
         "The prompt bar is slimmer and its text is centred. Conversation, Listen and Auto-speak are plain icons in "
         "the audio options, and hovering one tells you what it does."),
    49: ("Tidier links on phones",
         "On phones, the links under the prompt bar no longer end with a stray dot when they wrap onto two lines."),
    50: ("A clearer sign-in button in dark mode",
         "In dark mode, the Google sign-in button no longer sits in a white box."),
    48: ("Sign in with Google",
         "Sign in with your Google account to use Dia. Each person gets 10 runs a day, a counter shows how many are "
         "left, and the count resets at midnight Eastern time."),
    52: ("What changed, under the bell",
         "The bell at the top shows what changed. A dot means something is new. Rest on it or tap it for the latest "
         "changes, or choose See all changes for the full list."),
}

# Written ahead for pull requests that have no number yet. When one opens, add "N: UPCOMING[...]" to SUMMARY.
UPCOMING: dict[str, tuple[str, str]] = {
    "composer-v3": ("A lighter prompt bar",
                    "The prompt bar is smaller, with lighter see-through buttons. The mic sits in the right corner, "
                    "and the send button fades in beside it once you type. Audio options open in a small panel just "
                    "above the bar."),
}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def et_date(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).astimezone(ET).date().isoformat()


def merged_prs() -> list[dict]:
    """Merged pull requests reachable from HEAD, newest first (by commit time), one row per number."""
    out = git("log", "--date-order", "--format=%x1e%H%x1f%P%x1f%cI%x1f%s")
    rows, seen = [], set()
    for block in out.split("\x1e")[1:]:
        sha, parents, when, subject = block.strip("\n").split("\x1f", 3)
        m = MERGE_RE.match(subject) or SQUASH_RE.search(subject)
        if not m:
            continue
        number = int(m.group(1))
        if number < FIRST_PR or number in seen:
            continue
        seen.add(number)
        first_parent = parents.split()[0] if parents else None
        files = git("diff", "--name-only", first_parent, sha).split() if first_parent else []
        rows.append({"pr": number, "date": et_date(when), "files": files})
    return rows


def branch_files(base: str) -> list[str]:
    return git("diff", "--name-only", git("merge-base", base, "HEAD").strip(), "HEAD").split()


def check_text(text: str, where: str) -> None:
    hit = IDENTITY_RE.search(text) or FLEET_RE.search(text)
    if hit:
        raise SystemExit(f"build-changelog: {where} reads {hit.group(0)!r}; write it for a visitor, with no agent, "
                         "model or provider names")


def user_facing(files: list[str]) -> bool:
    return any(f.startswith(USER_FACING) and f not in OWN_FILES for f in files)


def build(pr: int | None = None, base: str = "origin/main", today: str | None = None) -> dict:
    rows = merged_prs()
    if pr is not None and pr not in {r["pr"] for r in rows}:
        today = today or dt.datetime.now(ET).date().isoformat()
        rows.insert(0, {"pr": pr, "date": today, "files": branch_files(base)})
    entries = []
    for row in rows:
        n = row["pr"]
        if n not in SUMMARY:
            if user_facing(row["files"]):
                raise SystemExit(f"build-changelog: #{n} changes what visitors see but has no line in SUMMARY "
                                 f"(scripts/build-changelog.py); add one, or None to leave it out")
            continue
        line = SUMMARY[n]
        if line is None:
            continue
        title, text = line
        check_text(title + " " + text, f"#{n}")
        entries.append({"key": f"pr-{n}", "pr": n, "date": row["date"], "title": title, "text": text})
    return {"about": "What changed in Dia, newest first. Built from the project's history by "
                     "scripts/build-changelog.py; do not edit by hand.",
            "entries": entries}


def render(**kw) -> str:
    return json.dumps(build(**kw), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if static/changelog.json is out of date")
    ap.add_argument("--pr", type=int, help="list the current branch as this pull request number (not merged yet)")
    ap.add_argument("--base", default="origin/main", help="base the --pr branch is compared against")
    a = ap.parse_args(argv)
    for title, text in (v for v in [*SUMMARY.values(), *UPCOMING.values()] if v):
        check_text(title + " " + text, "a SUMMARY line")
    js = render(pr=a.pr, base=a.base)
    if a.check:
        if not OUT_JSON.exists() or OUT_JSON.read_text(encoding="utf-8") != js:
            hint = f" --pr {a.pr}" if a.pr else ""
            print(f"STALE  static/changelog.json: run `python scripts/build-changelog.py{hint}` and commit the result")
            return 1
        print(f"OK     changelog current ({len(json.loads(js)['entries'])} entries)")
        return 0
    OUT_JSON.write_bytes(js.encode("utf-8"))
    print(f"written: static/changelog.json ({len(json.loads(js)['entries'])} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
