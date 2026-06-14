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
PLACEHOLDERS = ("TBD", "Unknown", "None listed", "None provided")
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


def evaluate(golden_map: dict) -> tuple[float, float, list[FixtureResult]]:
    """Run every fixture; per-fixture exceptions score 0 instead of raising."""
    results: list[FixtureResult] = []
    for path in sorted(FIXTURES_DIR.glob("*.md")):
        r = FixtureResult(name=path.name)
        try:
            run = run_fixture(path)
            if path.name in golden_map:
                r.golden_fraction, golden_failures = check_golden(
                    run, golden_map[path.name]
                )
            else:
                golden_failures = ["missing_golden_entry"]
            r.rubric_fraction, rubric_failures = check_rubric(run)
            r.failures = golden_failures + rubric_failures
        except Exception as exc:  # mutated pipeline may raise anything
            r.failures = [f"exception: {exc!r}"]
        results.append(r)

    count = len(results) or 1
    golden_pts = sum(r.golden_fraction for r in results) / count * GOLDEN_WEIGHT
    rubric_pts = sum(r.rubric_fraction for r in results) / count * RUBRIC_WEIGHT
    return golden_pts, rubric_pts, results


def bootstrap() -> dict:
    """Snapshot CURRENT pipeline behavior as a golden draft.

    Aspirational overrides are applied by hand afterwards -- see
    docs/superpowers/specs/2026-06-10-dia-autoresearch-design.md.
    """
    snapshot: dict = {}
    for path in sorted(FIXTURES_DIR.glob("*.md")):
        run = run_fixture(path)
        score, intake = run["score"], run["intake"]
        entry = {
            "expected_tier": score.tier,
            "expected_score_range": [score.total_score - 2, score.total_score + 2],
            "expected_issues": sorted(issue.code for issue in run["issues"]),
            "must_parse": {
                "pain_points_count": len(intake.pain_points),
                "goals_count": len(intake.goals),
            },
        }
        if intake.company_name.strip():
            entry["must_parse"]["company_name"] = intake.company_name
        snapshot[path.name] = entry
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true",
                        help="write a current-behavior snapshot to golden.json")
    parser.add_argument("--force", action="store_true",
                        help="allow --bootstrap to overwrite an existing golden.json")
    parser.add_argument("--verbose", action="store_true",
                        help="print per-fixture failures")
    args = parser.parse_args(argv)

    if args.bootstrap:
        if GOLDEN_PATH.exists() and not args.force:
            print(f"refusing to overwrite {GOLDEN_PATH}; rerun with --force")
            return 1
        GOLDEN_PATH.write_text(
            json.dumps(bootstrap(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {GOLDEN_PATH}")
        return 0

    if not GOLDEN_PATH.exists():
        print(f"no golden.json at {GOLDEN_PATH}; run --bootstrap first")
        return 1

    start = time.perf_counter()
    golden_map = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden_pts, rubric_pts, results = evaluate(golden_map)
    elapsed = time.perf_counter() - start

    if args.verbose:
        for r in results:
            if r.failures:
                print(f"[{r.name}] golden={r.golden_fraction:.2f} "
                      f"rubric={r.rubric_fraction:.2f} "
                      f"failures={', '.join(r.failures)}")
        print("---")
    print(f"dia_score:       {golden_pts + rubric_pts:.2f}")
    print(f"golden_pts:      {golden_pts:.2f} / {GOLDEN_WEIGHT:.0f}")
    print(f"rubric_pts:      {rubric_pts:.2f} / {RUBRIC_WEIGHT:.0f}")
    print(f"fixtures:        {len(results)}")
    print(f"runtime_seconds: {elapsed:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
