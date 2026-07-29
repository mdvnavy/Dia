"""Empirical stress test suite for dia-engine policy evaluation, agent-eggs engine_pointer resolution,
and run_engine_pointer() execution handler.

Created by teamwork_preview_challenger_1 for navy.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from dia_engine.core import evaluate_policy, load_rules_from_source
from eggshell import packer
from digestion.digester import Digester


# =====================================================================
# Helper Fixtures and Utility Functions
# =====================================================================

@pytest.fixture
def sample_valid_rule() -> dict[str, Any]:
    return {
        "rule_id": "TEST-RULE-001",
        "title": "Test Policy Rule",
        "source": "Ontario Education Act s. 264",
        "effective_date": "2026-01-01",
        "jurisdiction": "Ontario, Canada",
        "public_vs_internal": "PUBLIC",
        "domain_category": "Pedagogical",
        "normative_level": "MUST",
        "summary": "Teachers shall prepare and conduct lessons according to policy.",
        "verbatim_excerpt": "It is the duty of a teacher to instruction pupils.",
        "escalation_trigger_ids": ["ESC-TEST-001"],
        "metadata": {"authoring_agency": "EDU Ministry", "version": "1.0.0"},
    }


def create_mock_egg_file(
    dest_path: Path,
    manifest_data: dict[str, Any],
    payload_files: dict[str, str] | None = None,
    with_payload_boundary: bool = True,
) -> Path:
    """Helper to write a custom .egg file with YAML header and zip payload."""
    header_yaml = yaml.dump(manifest_data, default_flow_style=False)
    content = header_yaml.encode("utf-8")
    if with_payload_boundary:
        content += b"\n---\n"

    # Append zip payload if boundary present
    if with_payload_boundary:
        zip_buf = tempfile.NamedTemporaryFile(delete=False)
        zip_buf_path = Path(zip_buf.name)
        zip_buf.close()
        try:
            with zipfile.ZipFile(zip_buf_path, "w") as zf:
                if payload_files:
                    for filename, file_content in payload_files.items():
                        zf.writestr(filename, file_content)
                else:
                    zf.writestr("data/dummy.txt", "dummy payload content")
            payload_bytes = zip_buf_path.read_bytes()
        finally:
            if zip_buf_path.exists():
                zip_buf_path.unlink()

        content += payload_bytes

    dest_path.write_bytes(content)
    return dest_path


# =====================================================================
# SECTION 1: dia-engine Policy Evaluation Edge Cases & Stress Tests
# =====================================================================

class TestDiaEnginePolicyEvaluation:

    def test_evaluate_policy_malformed_query_types(self, sample_valid_rule: dict[str, Any]):
        """Passing non-string query types must raise AttributeError or TypeError cleanly."""
        rules = [sample_valid_rule]

        invalid_queries = [
            None,
            12345,
            3.14159,
            ["query_in_list"],
            {"query_key": "query_val"},
            True,
        ]

        for invalid_q in invalid_queries:
            with pytest.raises((AttributeError, TypeError)):
                evaluate_policy(invalid_q, egg_source=rules)

    def test_evaluate_policy_empty_and_whitespace_query(self, sample_valid_rule: dict[str, Any]):
        """Empty string or whitespace-only queries should evaluate without crashing."""
        rules = [sample_valid_rule]

        for empty_q in ["", "   ", "\t\n  \r"]:
            result = evaluate_policy(empty_q, egg_source=rules)
            assert result is not None
            assert result["cited_rule_id"] == "TEST-RULE-001"
            assert "answer" in result
            assert "provenance" in result

    def test_evaluate_policy_extreme_length_query(self, sample_valid_rule: dict[str, Any]):
        """Stress test evaluate_policy with large input query strings (500k chars)."""
        rules = [sample_valid_rule]
        large_query = "What is the policy regarding duties? " + ("word " * 100000)

        result = evaluate_policy(large_query, egg_source=rules)
        assert result["cited_rule_id"] == "TEST-RULE-001"

    def test_evaluate_policy_special_characters_and_injections(self, sample_valid_rule: dict[str, Any]):
        """Query containing regex chars, SQL injection, HTML, null bytes, and unicode emojis."""
        rules = [sample_valid_rule]

        adversarial_queries = [
            r"What is (.*+?^${}()|[]) policy?",
            "SELECT * FROM rules WHERE '1'='1'; -- DROP TABLE rules;",
            "<script>alert('XSS_ATTACK');</script><h1>Heading</h1>",
            "Null byte test \x00\x07\x1b control char",
            "Policy 48 🛡️ test 评估 \u2603 \U0001f600 unicode boundary",
        ]

        for adv_q in adversarial_queries:
            result = evaluate_policy(adv_q, egg_source=rules)
            assert result["cited_rule_id"] == "TEST-RULE-001"

    def test_evaluate_policy_empty_rules_list_raises_value_error(self):
        """Evaluating against an empty rules list must raise ValueError."""
        with pytest.raises(ValueError, match="No policy rules loaded for evaluation"):
            evaluate_policy("Any question", egg_source=[])

    def test_evaluate_policy_empty_rules_dict_raises_value_error(self):
        """Evaluating against a dict with empty rules list must raise ValueError."""
        with pytest.raises(ValueError, match="No policy rules loaded for evaluation"):
            evaluate_policy("Any question", egg_source={"rules": []})

    def test_evaluate_policy_empty_directory_raises_value_error(self):
        """Evaluating against an empty directory containing no .yaml rule files raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with pytest.raises(ValueError, match="No policy rules loaded for evaluation"):
                evaluate_policy("Any question", egg_source=Path(tmp_dir))

    def test_load_rules_from_source_corrupted_or_zero_byte_egg(self):
        """Loading rules from a 0-byte or corrupted .egg file raises zipfile.BadZipFile."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_egg = Path(tmp_dir) / "empty.egg"
            empty_egg.touch()

            with pytest.raises(zipfile.BadZipFile):
                load_rules_from_source(empty_egg)

            corrupt_egg = Path(tmp_dir) / "corrupt.egg"
            corrupt_egg.write_bytes(b"INVALID_EGG_HEADER_NO_ZIP_PAYLOAD\n---\nGARBAGE_ZIP")
            with pytest.raises(zipfile.BadZipFile):
                load_rules_from_source(corrupt_egg)

    def test_load_rules_from_source_malformed_yaml_directory(self):
        """Directory containing invalid YAML files or YAML files without 'rules' key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            rules_dir = Path(tmp_dir) / "rules"
            rules_dir.mkdir()

            # Malformed YAML syntax
            bad_yaml = rules_dir / "bad_syntax.yaml"
            bad_yaml.write_text("rules: [unclosed_list: {", encoding="utf-8")

            with pytest.raises(yaml.YAMLError):
                load_rules_from_source(Path(tmp_dir))

    def test_evaluate_policy_missing_mandatory_rule_fields(self):
        """Rules missing mandatory fields (rule_id, source, etc.) raise KeyError upon result building."""
        mandatory_fields = [
            "rule_id",
            "source",
            "effective_date",
            "jurisdiction",
            "public_vs_internal",
            "summary",
            "verbatim_excerpt",
        ]

        base_rule = {
            "rule_id": "TEST-RULE-001",
            "title": "Test Title",
            "source": "Act s. 1",
            "effective_date": "2026-01-01",
            "jurisdiction": "Ontario",
            "public_vs_internal": "PUBLIC",
            "summary": "Test summary",
            "verbatim_excerpt": "Test verbatim excerpt",
        }

        for missing_field in mandatory_fields:
            incomplete_rule = dict(base_rule)
            del incomplete_rule[missing_field]

            with pytest.raises(KeyError) as exc_info:
                evaluate_policy("Test question", egg_source=[incomplete_rule])
            assert missing_field in str(exc_info.value)

    def test_evaluate_policy_missing_optional_rule_fields(self):
        """Rules missing optional fields fall back gracefully without KeyError."""
        minimal_rule = {
            "rule_id": "MINIMAL-001",
            "source": "Minimal Source",
            "effective_date": "2026-01-01",
            "jurisdiction": "Ontario",
            "public_vs_internal": "PUBLIC",
            "summary": "Minimal summary",
            "verbatim_excerpt": "Minimal excerpt",
        }

        result = evaluate_policy("Minimal question", egg_source=[minimal_rule])
        assert result["cited_rule_id"] == "MINIMAL-001"
        assert result["title"] == ""
        assert result["domain_category"] == ""
        assert result["normative_level"] == "MUST"
        assert result["escalation_triggers"] == []
        assert result["provenance"]["authoring_agency"] == ""
        assert result["provenance"]["version"] == "1.0.0"

    def test_evaluate_policy_concurrent_stress(self, sample_valid_rule: dict[str, Any]):
        """Stress test concurrent evaluations from 50 worker threads."""
        rules = [sample_valid_rule]
        questions = [
            f"Question {i} regarding duties and instruction"
            for i in range(100)
        ]

        def worker(q: str):
            return evaluate_policy(q, egg_source=rules)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            results = list(executor.map(worker, questions))

        assert len(results) == 100
        for res in results:
            assert res["cited_rule_id"] == "TEST-RULE-001"


# =====================================================================
# SECTION 2: agent-eggs engine_pointer Resolution Edge Cases
# =====================================================================

class TestEnginePointerResolution:

    def test_parse_engine_pointer_missing_or_none(self):
        """Manifest with no engine_pointer returns None."""
        assert packer.parse_engine_pointer({}) is None
        assert packer.parse_engine_pointer({"name": "test"}) is None
        assert packer.parse_engine_pointer({"engine_pointer": None}) is None

    def test_parse_engine_pointer_non_dict_types(self):
        """engine_pointer set to non-dict types raises ValueError."""
        invalid_pointers = [
            "dia-engine.core:evaluate_policy",
            ["dia-engine"],
            12345,
            True,
        ]

        for inv in invalid_pointers:
            with pytest.raises(ValueError, match="engine_pointer must be a dictionary mapping"):
                packer.parse_engine_pointer({"engine_pointer": inv})

    def test_parse_engine_pointer_missing_required_fields(self):
        """Missing mandatory keys in engine_pointer raises ValueError specifying the missing key."""
        base_pointer = {
            "name": "dia-engine",
            "version": "^1.0.0",
            "entry_point": "dia_engine.core:evaluate_policy",
        }

        required_keys = ["name", "version", "entry_point"]
        for key in required_keys:
            incomplete = dict(base_pointer)
            del incomplete[key]

            with pytest.raises(ValueError, match=f"engine_pointer missing required field: {key}"):
                packer.parse_engine_pointer({"engine_pointer": incomplete})

    def test_parse_engine_pointer_valid_schema(self):
        """Valid engine_pointer parses successfully."""
        manifest = {
            "name": "edu-domain-egg",
            "engine_pointer": {
                "name": "dia-engine",
                "version": "^1.0.0",
                "entry_point": "dia_engine.core:evaluate_policy",
                "runtime_requirements": {
                    "python": ">=3.11",
                    "environment": {"DIA_POLICY_MODE": "strict"},
                },
            },
        }

        pointer = packer.parse_engine_pointer(manifest)
        assert pointer is not None
        assert pointer["name"] == "dia-engine"
        assert pointer["version"] == "^1.0.0"
        assert pointer["entry_point"] == "dia_engine.core:evaluate_policy"
        assert pointer["runtime_requirements"]["python"] == ">=3.11"

    def test_peek_missing_payload_boundary_raises_value_error(self):
        """peek() on file missing \\n---\\n raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            no_boundary_file = Path(tmp_dir) / "no_boundary.egg"
            no_boundary_file.write_text("name: test-egg\nversion: 1.0.0\n", encoding="utf-8")

            with pytest.raises(ValueError, match=r"No payload boundary \(\\n---\\n\)"):
                packer.peek(no_boundary_file)

    def test_peek_corrupted_yaml_front_matter(self):
        """peek() on invalid YAML header raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_yaml_file = Path(tmp_dir) / "bad_yaml.egg"
            bad_yaml_file.write_bytes(b"[unclosed_yaml: {\n---\n")

            with pytest.raises(yaml.YAMLError):
                packer.peek(bad_yaml_file)


# =====================================================================
# SECTION 3: Digester.run_engine_pointer() Execution Handler Edge Cases
# =====================================================================

class TestDigesterRunEnginePointer:

    def test_run_engine_pointer_missing_engine_pointer_raises_value_error(self):
        """Calling run_engine_pointer on egg with no engine_pointer raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            egg_path = Path(tmp_dir) / "no_pointer.egg"
            create_mock_egg_file(egg_path, manifest_data={"name": "no-pointer-egg", "version": "1.0.0"})

            digester = Digester(egg_path)
            with pytest.raises(ValueError, match="Manifest contains no valid engine_pointer"):
                digester.run_engine_pointer()

    def test_run_engine_pointer_invalid_schema_raises_value_error(self):
        """Calling run_engine_pointer on egg with incomplete engine_pointer raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            egg_path = Path(tmp_dir) / "bad_schema.egg"
            manifest = {
                "name": "bad-schema-egg",
                "engine_pointer": {
                    "name": "dia-engine",
                    # missing entry_point and version
                },
            }
            create_mock_egg_file(egg_path, manifest_data=manifest)

            digester = Digester(egg_path)
            with pytest.raises(ValueError, match="engine_pointer missing required field"):
                digester.run_engine_pointer()

    def test_run_engine_pointer_subprocess_execution(self):
        """run_engine_pointer executes python -m <entry_point> with environment variables."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            egg_path = Path(tmp_dir) / "valid_pointer.egg"
            manifest = {
                "name": "valid-pointer-egg",
                "engine_pointer": {
                    "name": "python-module",
                    "version": "1.0.0",
                    "entry_point": "json.tool",
                    "runtime_requirements": {
                        "environment": {"TEST_ENV_VAR_EGG": "HEALTHY_EGG_ENV_123"}
                    },
                },
            }
            create_mock_egg_file(egg_path, manifest_data=manifest)

            digester = Digester(egg_path)
            result = digester.run_engine_pointer(["--help"])

            assert result is not None
            assert result.returncode == 0
            assert "json.tool" in result.stdout or "usage:" in result.stdout.lower()

    def test_run_engine_pointer_nonexistent_module(self):
        """run_engine_pointer with non-existent module returns returncode != 0 and captures stderr."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            egg_path = Path(tmp_dir) / "nonexistent_module.egg"
            manifest = {
                "name": "nonexistent-module-egg",
                "engine_pointer": {
                    "name": "nonexistent",
                    "version": "1.0.0",
                    "entry_point": "nonexistent_module_xyz_999",
                },
            }
            create_mock_egg_file(egg_path, manifest_data=manifest)

            digester = Digester(egg_path)
            result = digester.run_engine_pointer()

            assert result is not None
            assert result.returncode != 0
            assert "No module named" in result.stderr

    def test_digester_concurrent_engine_pointer_evaluations(self):
        """Multiple threads invoking Digester concurrently must not collide on temp workspaces."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            egg_paths: list[Path] = []
            for i in range(10):
                p = Path(tmp_dir) / f"egg_{i}.egg"
                manifest = {
                    "name": f"concurrent-egg-{i}",
                    "engine_pointer": {
                        "name": "python-module",
                        "version": "1.0.0",
                        "entry_point": "json.tool",
                    },
                }
                create_mock_egg_file(p, manifest_data=manifest)
                egg_paths.append(p)

            def digester_worker(path: Path):
                d = Digester(path)
                return d.run_engine_pointer(["--help"])

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(digester_worker, egg_paths))

            assert len(results) == 10
            for r in results:
                assert r.returncode == 0
