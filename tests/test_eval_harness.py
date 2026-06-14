"""Build-time tests for the autoresearch eval harness (eval/run_eval.py).

The harness is READ-ONLY to the experiment loop; these tests protect it
via the test gate. Loaded with importlib because the eval/ directory is
deliberately not a package.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "dia_run_eval", REPO_ROOT / "eval" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(_spec)
# Register before exec_module: CPython 3.13 dataclasses._is_type calls
# sys.modules.get(cls.__module__).__dict__ with no None-guard, so the module
# must already be in sys.modules before @dataclass processes the class body.
sys.modules[_spec.name] = run_eval
_spec.loader.exec_module(run_eval)

FIXTURES = REPO_ROOT / "tests" / "e2e" / "fixtures" / "inputs"


def test_run_fixture_complete_intake():
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    assert result["score"].tier == "Custom AI Agent"
    assert 10 <= result["score"].total_score <= 14  # golden band; scoring is loop-mutable
    assert result["issues"] == []
    assert set(result["docs"]) == {
        "client-profile.md", "opportunity-analysis.md", "proposal-draft.md"
    }


def test_check_golden_all_pass():
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    golden = {
        "expected_tier": "Custom AI Agent",
        "expected_score_range": [10, 14],
        "expected_issues": [],
        "must_parse": {
            "company_name": "Northstar Studio",
            "pain_points_count": 2,
            "goals_count": 2,
        },
    }
    fraction, failures = run_eval.check_golden(result, golden)
    assert fraction == 1.0
    assert failures == []


def test_check_golden_detects_wrong_tier_and_issues():
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    golden = {
        "expected_tier": "Quick Win",                # wrong on purpose
        "expected_score_range": [10, 14],
        "expected_issues": ["missing_budget"],       # wrong on purpose
        "must_parse": {"company_name": "Northstar Studio"},
    }
    fraction, failures = run_eval.check_golden(result, golden)
    assert "tier" in failures
    assert "issues" in failures
    assert fraction < 1.0


@pytest.mark.baseline_pin
def test_check_rubric_flags_pain_grounding_at_baseline():
    # The current proposal template never cites pain points -- this is
    # intentional headroom for the experiment loop.
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    fraction, failures = run_eval.check_rubric(result)
    assert "pain_grounding" in failures
    assert 0.0 < fraction < 1.0


def test_check_rubric_passes_on_grounded_docs():
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    intake = result["intake"]
    # Simulate an improved template: inject the first pain point into the
    # proposal so grounding passes.
    result["docs"]["proposal-draft.md"] += f"\n## Why Now\n{intake.pain_points[0]}\n"
    fraction, failures = run_eval.check_rubric(result)
    assert "pain_grounding" not in failures


@pytest.mark.baseline_pin
def test_check_rubric_flags_placeholder_leak():
    result = run_eval.run_fixture(FIXTURES / "minimal_intake.md")
    fraction, failures = run_eval.check_rubric(result)
    # minimal intake is missing fields, so today's templates leak TBD/None
    assert "no_placeholders" in failures


@pytest.mark.baseline_pin
def test_bootstrap_covers_every_fixture():
    snapshot = run_eval.bootstrap()
    fixture_names = {p.name for p in FIXTURES.glob("*.md")}
    assert set(snapshot) == fixture_names
    entry = snapshot["complete_intake.md"]
    assert entry["expected_tier"] == "Custom AI Agent"
    assert entry["expected_score_range"] == [10, 14]   # total 12, +/- 2
    assert entry["expected_issues"] == []
    assert entry["must_parse"]["company_name"] == "Northstar Studio"


def test_evaluate_handles_broken_fixture_without_crashing(tmp_path, monkeypatch):
    # A fixture that explodes the pipeline must score 0, not kill the harness.
    bad = tmp_path / "explodes.md"
    bad.write_text("anything", encoding="utf-8")
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    def boom(path):
        raise ValueError("pipeline exploded")

    monkeypatch.setattr(run_eval, "run_fixture", boom)
    golden_pts, rubric_pts, results = run_eval.evaluate({"explodes.md": {}})
    assert golden_pts == 0.0
    assert rubric_pts == 0.0
    assert results[0].failures and "exception" in results[0].failures[0]


@pytest.mark.baseline_pin
def test_baseline_dia_score_in_expected_band():
    golden_map = json.loads(
        (REPO_ROOT / "eval" / "golden.json").read_text(encoding="utf-8")
    )
    golden_pts, rubric_pts, results = run_eval.evaluate(golden_map)
    dia_score = golden_pts + rubric_pts
    # Aspirational goldens + rubric headroom: baseline must sit below a
    # perfect score but well above half. Bounds are deliberately loose so
    # ordinary refactors don't trip this; the experiment loop is what
    # should move the number.
    assert 60.0 <= dia_score < 96.0, f"baseline dia_score={dia_score:.2f}"
    assert len(results) == 12
