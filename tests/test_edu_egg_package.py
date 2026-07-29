"""Automated verification test suite for Milestone 4: EDU Domain Egg Packaging & Human Escalation Guardrails."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest
import yaml

from eggshell import packer


DIA_DIR = Path(__file__).parent.parent
EDU_SOURCE_DIR = DIA_DIR / "edu_egg_source"
DIST_DIR = DIA_DIR / "dist"
EGG_PATH = DIST_DIR / "edu-domain-egg-1.0.0.egg"


def test_rules_directory_contains_six_modular_files():
    rules_dir = EDU_SOURCE_DIR / "rules"
    assert rules_dir.exists(), "rules directory must exist"
    expected_files = {
        "accessibility_aoda.yaml",
        "internal_pedagogical_rules.yaml",
        "pdsb_board_policies.yaml",
        "privacy_mfippa.yaml",
        "provincial_statutes.yaml",
        "union_cba_limits.yaml",
    }
    actual_files = {f.name for f in rules_dir.glob("*.yaml")}
    assert expected_files.issubset(actual_files), f"Missing rule files: {expected_files - actual_files}"


def test_human_escalation_guardrails_yaml():
    guardrails_file = EDU_SOURCE_DIR / "guardrails" / "human_escalation_guardrails.yaml"
    assert guardrails_file.exists(), "human_escalation_guardrails.yaml must exist"

    data = yaml.safe_load(guardrails_file.read_text(encoding="utf-8"))
    assert "triggers" in data, "guardrails YAML must contain triggers list"

    triggers = data["triggers"]
    assert len(triggers) == 6, f"Expected 6 escalation triggers, found {len(triggers)}"

    trigger_ids = {t["trigger_id"] for t in triggers}
    expected_trigger_ids = {
        "ESC-AMBIGUITY-001",
        "ESC-PRIVACY-BREACH-001",
        "ESC-DISCIPLINE-SAFE-001",
        "ESC-HUMAN-RIGHTS-001",
        "ESC-AI-UNVETTED-002",
        "ESC-UNION-SURVEILLANCE-001",
    }
    assert trigger_ids == expected_trigger_ids, f"Trigger ID mismatch: expected {expected_trigger_ids}, got {trigger_ids}"

    for trigger in triggers:
        assert "category" in trigger
        assert "name" in trigger
        assert "description" in trigger
        assert "severity" in trigger
        assert "recommended_human_role" in trigger
        assert "mandatory_action" in trigger


def test_escalation_payload_schema_json():
    schema_file = EDU_SOURCE_DIR / "guardrails" / "escalation_payload_schema.json"
    assert schema_file.exists(), "escalation_payload_schema.json must exist"

    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    assert schema.get("type") == "object"
    required_fields = set(schema.get("required", []))
    expected_fields = {
        "escalation_id",
        "trigger_id",
        "severity",
        "recommended_human_role",
        "mandatory_action",
    }
    assert expected_fields.issubset(required_fields), f"Missing required fields in schema: {expected_fields - required_fields}"


def test_refs_directory_contains_three_reference_docs():
    refs_dir = EDU_SOURCE_DIR / "refs"
    assert refs_dir.exists(), "refs directory must exist"
    expected_docs = {
        "ontario_edu_framework_ref.md",
        "pdsb_policy_manual_ref.md",
        "grade6_spatial_sense_ref.md",
    }
    actual_docs = {f.name for f in refs_dir.glob("*.md")}
    assert expected_docs.issubset(actual_docs), f"Missing reference docs: {expected_docs - actual_docs}"


def test_front_matter_yaml():
    front_matter_file = EDU_SOURCE_DIR / "front-matter.yaml"
    assert front_matter_file.exists(), "front-matter.yaml must exist"

    data = yaml.safe_load(front_matter_file.read_text(encoding="utf-8"))
    assert data.get("name") == "edu-domain-egg"
    assert data.get("type") == "domain"
    assert data.get("version") == "1.0.0"

    engine_pointer = data.get("engine_pointer")
    assert engine_pointer is not None, "engine_pointer block must be present"
    assert engine_pointer.get("name") == "dia-engine"
    assert engine_pointer.get("version") == "^1.0.0"
    assert engine_pointer.get("entry_point") == "dia_engine.core:evaluate_policy"
    assert "runtime_requirements" in engine_pointer
    assert engine_pointer["runtime_requirements"].get("python") == ">=3.11"
    assert engine_pointer["runtime_requirements"]["environment"].get("DIA_POLICY_MODE") == "strict"


def test_egg_artifact_packaging_and_verification():
    assert EGG_PATH.exists(), f"Packaged egg file must exist at {EGG_PATH}"

    # Peek test
    header = packer.peek(EGG_PATH)
    assert header.get("name") == "edu-domain-egg"
    assert header.get("type") == "domain"
    assert header.get("version") == "1.0.0"

    # Engine pointer parsing test
    pointer = packer.parse_engine_pointer(header)
    assert pointer is not None
    assert pointer["name"] == "dia-engine"
    assert pointer["entry_point"] == "dia_engine.core:evaluate_policy"

    # Checksum verification test
    assert packer.verify(EGG_PATH) is True

    # Extraction test
    with tempfile.TemporaryDirectory() as tmp_dir:
        dest = Path(tmp_dir)
        packer.extract(EGG_PATH, dest)
        extracted_rules = list((dest / "rules").glob("*.yaml"))
        assert len(extracted_rules) == 6
        extracted_guardrails = list((dest / "guardrails").iterdir())
        assert len(extracted_guardrails) >= 2
        extracted_refs = list((dest / "refs").glob("*.md"))
        assert len(extracted_refs) == 3
