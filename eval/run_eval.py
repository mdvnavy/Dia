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
