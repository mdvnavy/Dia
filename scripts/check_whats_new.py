"""CI check for static/whats-new.json (spec proto/whats-new-indicator.md v0.3 §7, §8, acceptance test 17).

Fails on: invalid schema, duplicate ids, title > 70 or body > 320 chars, or a banned (internal/ops) word in a
live_flag: true note. With --pr-author/--changed-files it also fails a Jules-authored PR that touches the file.

    python scripts/check_whats_new.py
    python scripts/check_whats_new.py --pr-author "$LOGIN" --changed-files changed.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES_PATH = ROOT / "static" / "whats-new.json"
NOTES_REL = "static/whats-new.json"
TITLE_MAX = 70
BODY_MAX = 320
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Spec §7 list. "Bash" means the agent/handle; the company name "Bash Systems" (in Ikenna's approved N1 copy) is allowed.
BANNED = re.compile(
    r"Cloud Run|\brevisions?\b|\benv\b|MEMORY_SHEET|Vertex|\bADK\b|PR #|\bbranch(?:es)?\b|gcloud|\bForge\b"
    r"|\bBash\b(?! Systems)|\bJules\b|coming soon",
    re.IGNORECASE,
)
REQUIRED = {"id": str, "date": str, "title": str, "body": str, "app": str, "live_flag": bool}
APPS = {"dia", "tia"}
JULES_AUTHOR = re.compile(r"jules", re.IGNORECASE)


def validate(data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("notes"), list):
        return ["top level must be {\"version\": 1, \"notes\": [...]}"]
    seen: set[str] = set()
    for i, note in enumerate(data["notes"]):
        where = f"notes[{i}]"
        if not isinstance(note, dict):
            errors.append(f"{where}: must be an object")
            continue
        for key, kind in REQUIRED.items():
            if not isinstance(note.get(key), kind):
                errors.append(f"{where}: '{key}' must be {kind.__name__}")
        note_id = note.get("id")
        if isinstance(note_id, str):
            where = f"note {note_id!r}"
            if not ID_RE.match(note_id):
                errors.append(f"{where}: id must be YYYY-MM-DD-slug")
            if note_id in seen:
                errors.append(f"{where}: duplicate id")
            seen.add(note_id)
        if isinstance(note.get("date"), str) and not DATE_RE.match(note["date"]):
            errors.append(f"{where}: date must be YYYY-MM-DD")
        if note.get("app") not in APPS:
            errors.append(f"{where}: app must be one of {sorted(APPS)}")
        if isinstance(note.get("title"), str) and len(note["title"]) > TITLE_MAX:
            errors.append(f"{where}: title is {len(note['title'])} chars (max {TITLE_MAX})")
        if isinstance(note.get("body"), str) and len(note["body"]) > BODY_MAX:
            errors.append(f"{where}: body is {len(note['body'])} chars (max {BODY_MAX})")
        if note.get("live_flag") is True:
            text = f"{note.get('title', '')}\n{note.get('body', '')}"
            link = note.get("link")
            if isinstance(link, dict):
                text += f"\n{link.get('text', '')}"
            hit = BANNED.search(text)
            if hit:
                errors.append(f"{where}: banned word {hit.group(0)!r} in a live note")
            if "!" in text:
                errors.append(f"{where}: no exclamation marks in a live note")
            if not note.get("source"):
                errors.append(f"{where}: a live note needs a source (change-log entry or How it works row)")
            if note.get("approved_by") != "Ikenna" or not note.get("approved_at"):
                errors.append(f"{where}: a live note needs approved_by Ikenna and approved_at")
    return errors


def check_pr(author: str | None, changed_files: list[str]) -> list[str]:
    if author and JULES_AUTHOR.search(author) and NOTES_REL in {f.strip() for f in changed_files}:
        return [f"{NOTES_REL} can't be changed by a Jules-authored PR ({author}); a human writes release notes"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(NOTES_PATH))
    parser.add_argument("--pr-author", default=None)
    parser.add_argument("--changed-files", default=None, help="path to a newline-separated list of changed files")
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"whats-new: can't read {args.file}: {error}")
        return 1
    errors = validate(data)
    if args.changed_files:
        errors += check_pr(args.pr_author, Path(args.changed_files).read_text(encoding="utf-8").splitlines())
    for error in errors:
        print(f"whats-new: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
