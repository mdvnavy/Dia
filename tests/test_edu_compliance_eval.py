"""
Automated Deterministic Test Suite & Provenance Compliance Evaluator for edu-domain-egg.
Milestone 5 (Part A): Evaluates 100% citation accuracy, rule provenance matching,
and human escalation trigger firing across all 6 policy domains.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
import pytest
import yaml

DIA_DIR = Path(__file__).resolve().parent.parent
if str(DIA_DIR) not in sys.path:
    sys.path.insert(0, str(DIA_DIR))

import dia_engine
from eggshell import packer

DIA_DIR = Path(__file__).resolve().parent.parent
EDU_SOURCE_DIR = DIA_DIR / "edu_egg_source"
TEST_PAIRS_FILE = EDU_SOURCE_DIR / "tests" / "deterministic_test_pairs.yaml"
DIST_DIR = DIA_DIR / "dist"
EGG_PATH = DIST_DIR / "edu-domain-egg-1.0.0.egg"


def load_test_pairs() -> list[dict[str, Any]]:
    """Load deterministic test pairs from YAML manifest."""
    assert TEST_PAIRS_FILE.exists(), f"Test pairs file missing at {TEST_PAIRS_FILE}"
    data = yaml.safe_load(TEST_PAIRS_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "test_pairs" in data, "Invalid test pairs YAML structure"
    pairs = data["test_pairs"]
    assert len(pairs) >= 12, f"Expected at least 12 test pairs, found {len(pairs)}"
    return pairs


def evaluate_compliance_pair(question: str, engine: Any = None, egg: Any = None) -> dict[str, Any]:
    """Invoke dia_engine policy evaluation and return evaluated compliance result.

    Verifies citation presence, provenance metadata completeness, and answer formatting.
    """
    if egg is None:
        egg = EGG_PATH if EGG_PATH.exists() else EDU_SOURCE_DIR

    if hasattr(engine, "evaluate_policy"):
        result = engine.evaluate_policy(question, egg)
    elif callable(engine):
        result = engine(question, egg)
    else:
        result = dia_engine.evaluate_policy(question, egg)

    # Core integrity & structural assertions
    assert isinstance(result, dict), "Evaluation result must be a dictionary"
    assert "cited_rule_id" in result and result["cited_rule_id"], "Result missing cited_rule_id"
    assert "source" in result and result["source"], "Result missing source citation"
    assert "effective_date" in result and result["effective_date"], "Result missing effective_date"
    assert "jurisdiction" in result and result["jurisdiction"], "Result missing jurisdiction"
    assert "public_vs_internal" in result and result["public_vs_internal"], "Result missing public_vs_internal"
    assert "answer" in result and result["answer"], "Result missing answer"

    # Provenance object completeness check
    assert "provenance" in result and isinstance(result["provenance"], dict), "Result missing provenance mapping"
    prov = result["provenance"]
    for field in ("source", "effective_date", "jurisdiction", "public_vs_internal"):
        assert field in prov, f"Provenance mapping missing field: {field}"
        assert prov[field] == result[field], f"Provenance mismatch for {field}: {prov[field]} vs {result[field]}"

    return result


def test_deterministic_pairs_count_and_domain_span():
    """Verify at least 12 test pairs exist spanning all 6 policy domains."""
    pairs = load_test_pairs()
    assert len(pairs) >= 12

    domains_covered = {p.get("domain") for p in pairs if "domain" in p}
    expected_domains = {
        "Provincial Statutes",
        "PDSB Policies",
        "Privacy / MFIPPA",
        "Accessibility / AODA",
        "Union CBA Limits",
        "Grade 6 Spatial Sense",
    }
    assert expected_domains.issubset(domains_covered), f"Missing domains: {expected_domains - domains_covered}"


def test_evaluate_compliance_pair_100_percent_accuracy():
    """Verify 100% citation accuracy, provenance matching, and trigger firing on all test pairs."""
    pairs = load_test_pairs()
    target_egg = EGG_PATH if EGG_PATH.exists() else EDU_SOURCE_DIR

    for pair in pairs:
        test_id = pair["test_id"]
        question = pair["question"]
        expected_rule = pair["expected_cited_rule"]
        expected_trigger = pair.get("expected_escalation_trigger")

        res = evaluate_compliance_pair(question, dia_engine, target_egg)

        # 1. Verification of 100% citation accuracy
        assert res["cited_rule_id"] == expected_rule, (
            f"Test {test_id} citation mismatch: expected {expected_rule}, got {res['cited_rule_id']}"
        )

        # 2. Rule provenance matching
        prov = res["provenance"]
        assert prov["source"] == res["source"]
        assert prov["effective_date"] == res["effective_date"]
        assert prov["jurisdiction"] in {"provincial_ontario", "board_pdsb"}
        assert prov["public_vs_internal"] in {"public", "internal"}

        # 3. Human escalation trigger firing verification
        if expected_trigger:
            assert expected_trigger in res["escalation_triggers"], (
                f"Test {test_id} missing expected escalation trigger '{expected_trigger}' in {res['escalation_triggers']}"
            )

        # 4. Deterministic answer content verification
        assert expected_rule in res["cited_rule_id"] or expected_rule in res["answer"]
        assert res["source"] in res["answer"]


def test_provenance_compliance_all_rules():
    """Verify provenance metadata consistency across all rules in the egg."""
    rules = dia_engine.load_rules_from_source(EDU_SOURCE_DIR)
    assert len(rules) >= 30, f"Expected at least 30 rules in domain egg, found {len(rules)}"

    for rule in rules:
        rule_id = rule["rule_id"]
        eval_res = dia_engine.evaluate_policy(f"Query for {rule_id}", EDU_SOURCE_DIR)
        assert eval_res["cited_rule_id"] == rule_id

        prov = eval_res["provenance"]
        assert prov["source"] == rule["source"]
        assert prov["effective_date"] == str(rule["effective_date"])
        assert prov["jurisdiction"] == rule["jurisdiction"]
        assert prov["public_vs_internal"] == rule["public_vs_internal"]


def test_egg_payload_includes_tests_directory():
    """Verify packaged egg artifact contains tests/deterministic_test_pairs.yaml in zip payload."""
    assert EGG_PATH.exists(), f"Packaged egg missing at {EGG_PATH}"
    assert packer.verify(EGG_PATH) is True

    with tempfile.TemporaryDirectory() as tmp_dir:
        dest = Path(tmp_dir)
        packer.extract(EGG_PATH, dest)
        extracted_pairs = dest / "tests" / "deterministic_test_pairs.yaml"
        assert extracted_pairs.exists(), "tests/deterministic_test_pairs.yaml missing from extracted egg payload"
        data = yaml.safe_load(extracted_pairs.read_text(encoding="utf-8"))
        assert len(data.get("test_pairs", [])) >= 12
