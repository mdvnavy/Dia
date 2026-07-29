"""
Unit tests for EDU Domain Egg policy rules metadata compliance.
Verifies 100% metadata compliance across all modular rule files.
"""

import os
import glob
import re
import yaml
import pytest
from pathlib import Path

RULES_DIR = Path("D:/_cultco/_agnt-ws/_repos/dia/edu_egg_source/rules")

REQUIRED_METADATA_KEYS = {
    "source",
    "effective_date",
    "jurisdiction",
    "public_vs_internal"
}

REQUIRED_ENTRY_KEYS = {
    "rule_id",
    "title",
    "source",
    "effective_date",
    "jurisdiction",
    "public_vs_internal",
    "domain_category",
    "normative_level",
    "summary",
    "verbatim_excerpt",
    "applicability",
    "cross_references",
    "escalation_trigger_ids",
    "metadata"
}

ALLOWED_JURISDICTIONS = {"provincial_ontario", "board_pdsb"}
ALLOWED_PUBLIC_VS_INTERNAL = {"public", "internal"}
ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}(/\d{4}-\d{2}-\d{2})?$")


def get_rule_files():
    pattern = str(RULES_DIR / "*.yaml")
    files = glob.glob(pattern)
    assert len(files) >= 6, f"Expected at least 6 YAML rule files in {RULES_DIR}, found {len(files)}"
    return files


def test_rule_files_exist():
    expected_files = [
        "provincial_statutes.yaml",
        "pdsb_board_policies.yaml",
        "privacy_mfippa.yaml",
        "accessibility_aoda.yaml",
        "union_cba_limits.yaml",
        "internal_pedagogical_rules.yaml"
    ]
    for filename in expected_files:
        path = RULES_DIR / filename
        assert path.exists(), f"Missing required rule file: {path}"


def test_all_rules_metadata_compliance():
    rule_files = get_rule_files()
    total_rules = 0

    for file_path in rule_files:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict), f"File {file_path} must be a dictionary with a 'rules' key"
        assert "rules" in data, f"File {file_path} missing top-level 'rules' key"
        rules = data["rules"]
        assert isinstance(rules, list) and len(rules) > 0, f"File {file_path} has no rule entries"

        for idx, rule in enumerate(rules):
            total_rules += 1
            rule_id = rule.get("rule_id", f"INDEX-{idx}")

            # 1. Check mandatory metadata keys exist
            missing_meta = REQUIRED_METADATA_KEYS - set(rule.keys())
            assert not missing_meta, f"Rule {rule_id} in {file_path} missing metadata keys: {missing_meta}"

            # 2. Check full required keys
            missing_keys = REQUIRED_ENTRY_KEYS - set(rule.keys())
            assert not missing_keys, f"Rule {rule_id} in {file_path} missing required keys: {missing_keys}"

            # 3. Validate jurisdiction value
            jurisdiction = rule["jurisdiction"]
            assert jurisdiction in ALLOWED_JURISDICTIONS, (
                f"Rule {rule_id} has invalid jurisdiction '{jurisdiction}'. Must be one of {ALLOWED_JURISDICTIONS}"
            )

            # 4. Validate public_vs_internal value
            p_vs_i = rule["public_vs_internal"]
            assert p_vs_i in ALLOWED_PUBLIC_VS_INTERNAL, (
                f"Rule {rule_id} has invalid public_vs_internal '{p_vs_i}'. Must be one of {ALLOWED_PUBLIC_VS_INTERNAL}"
            )

            # 5. Validate source exact citation is non-empty string
            source = rule["source"]
            assert isinstance(source, str) and len(source.strip()) > 5, (
                f"Rule {rule_id} has invalid or trivial source citation: '{source}'"
            )

            # 6. Validate effective_date format
            effective_date = str(rule["effective_date"])
            assert ISO_DATE_REGEX.match(effective_date), (
                f"Rule {rule_id} has invalid ISO effective_date '{effective_date}'"
            )

    print(f"PASS: Verified {total_rules} rules across {len(rule_files)} files with 100% metadata compliance.")


if __name__ == "__main__":
    test_rule_files_exist()
    test_all_rules_metadata_compliance()
