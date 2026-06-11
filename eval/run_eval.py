#!/usr/bin/env python
"""DIA autoresearch eval harness.

READ-ONLY to the experiment loop (see program-dia.md). Computes dia_score
(0-100, higher is better) over the intake fixtures using the deterministic
pipeline only -- zero network calls, zero API keys.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from client_discovery.core import (  # noqa: E402
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
)

FIXTURES_DIR = REPO_ROOT / "tests" / "e2e" / "fixtures" / "inputs"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden.json"
GOLDEN_WEIGHT = 60.0
RUBRIC_WEIGHT = 40.0


@dataclass
class FixtureResult:
    name: str
    golden_fraction: float = 0.0
    rubric_fraction: float = 0.0
    failures: list[str] = field(default_factory=list)


def run_fixture(path: Path) -> dict:
    """Run one fixture through the deterministic pipeline."""
    content = path.read_text(encoding="utf-8")
    intake = parse_questionnaire_markdown(content)
    return {
        "intake": intake,
        "issues": validate_intake(intake),
        "score": score_opportunity(intake),
        "docs": generate_documents(intake, score_opportunity(intake)),
    }


def check_golden(result: dict, golden: dict) -> tuple[float, list[str]]:
    """Score one fixture against its golden entry. Returns (fraction, failures)."""
    checks: list[tuple[str, bool]] = []
    score = result["score"]
    intake = result["intake"]

    checks.append(("tier", score.tier == golden["expected_tier"]))
    lo, hi = golden["expected_score_range"]
    checks.append(("score_range", lo <= score.total_score <= hi))
    actual_issues = sorted(issue.code for issue in result["issues"])
    checks.append(("issues", actual_issues == sorted(golden["expected_issues"])))

    for key, expected in golden.get("must_parse", {}).items():
        if key.endswith("_count"):
            fieldname = key[: -len("_count")]
            checks.append((key, len(getattr(intake, fieldname)) == expected))
        else:
            actual = getattr(intake, key)
            checks.append((key, str(expected).lower() in actual.lower()))

    failures = [name for name, ok in checks if not ok]
    return sum(ok for _, ok in checks) / len(checks), failures


REQUIRED_SECTIONS = {
    "client-profile.md": [
        "## Overview", "## Current Tools", "## Decision Context", "## Notes",
    ],
    "opportunity-analysis.md": [
        "## Pain Points", "## Goals", "## Recommendation", "## Score Reasons",
    ],
    "proposal-draft.md": [
        "## Executive Summary", "## Recommended Solution", "## Delivery Plan",
        "## Investment", "## Next Step",
    ],
}
PLACEHOLDERS = ("TBD", "Unknown", "None listed.", "None provided.")
DOC_LENGTH_BOUNDS = (200, 8000)


def check_rubric(result: dict) -> tuple[float, list[str]]:
    """Deterministic doc-quality rubric. Returns (fraction, failures)."""
    docs = result["docs"]
    intake = result["intake"]
    checks: list[tuple[str, bool]] = []

    for doc_name, sections in REQUIRED_SECTIONS.items():
        body = docs.get(doc_name, "")
        checks.append((f"sections:{doc_name}", all(s in body for s in sections)))

    all_text = "\n".join(docs.values())
    checks.append(
        ("no_placeholders", not any(p in all_text for p in PLACEHOLDERS))
    )
    if intake.company_name.strip():
        checks.append(
            ("company_grounding",
             all(intake.company_name in body for body in docs.values()))
        )
    if intake.pain_points:
        checks.append(
            ("pain_grounding",
             intake.pain_points[0] in docs.get("proposal-draft.md", ""))
        )
    if intake.goals:
        analysis = docs.get("opportunity-analysis.md", "")
        checks.append(
            ("goal_grounding", all(goal in analysis for goal in intake.goals))
        )
    lo, hi = DOC_LENGTH_BOUNDS
    checks.append(
        ("length_sanity", all(lo <= len(body) <= hi for body in docs.values()))
    )

    failures = [name for name, ok in checks if not ok]
    return sum(ok for _, ok in checks) / len(checks), failures
