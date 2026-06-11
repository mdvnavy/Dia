"""Build-time tests for the autoresearch eval harness (eval/run_eval.py).

The harness is READ-ONLY to the experiment loop; these tests protect it
via the test gate. Loaded with importlib because the eval/ directory is
deliberately not a package.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "dia_run_eval", REPO_ROOT / "eval" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)

FIXTURES = REPO_ROOT / "tests" / "e2e" / "fixtures" / "inputs"


def test_run_fixture_complete_intake():
    result = run_eval.run_fixture(FIXTURES / "complete_intake.md")
    assert result["score"].tier == "Custom AI Agent"
    assert result["score"].total_score == 12
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
