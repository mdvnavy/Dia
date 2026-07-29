from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


@dataclass
class DomainManifest:
    domain_name: str
    version: str
    description: str
    question_aliases: dict[str, list[str]] = field(default_factory=dict)
    section_queries: dict[str, dict[str, str]] = field(default_factory=dict)
    validation_rules: dict[str, Any] = field(default_factory=dict)
    scoring: dict[str, Any] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)
    agent_instruction: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> DomainManifest:
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(
            domain_name=data.get("domain_name", "unknown"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            question_aliases=data.get("question_aliases", {}),
            section_queries=data.get("section_queries", {}),
            validation_rules=data.get("validation_rules", {}),
            scoring=data.get("scoring", {}),
            templates=data.get("templates", {}),
            agent_instruction=data.get("agent_instruction", ""),
        )


class DomainEngine:
    def __init__(self, manifest: DomainManifest):
        self.manifest = manifest

    def parse_questionnaire_markdown(self, content: str):
        from .intake import parse_questionnaire_markdown
        return parse_questionnaire_markdown(content, self.manifest)

    def validate_intake(self, intake):
        from .validation import validate_intake
        return validate_intake(intake, self.manifest)

    def score_opportunity(self, intake):
        from .scoring import score_opportunity
        return score_opportunity(intake, self.manifest)

    def generate_documents(self, intake, score, strategic_analysis: str | None = None):
        from .documents import generate_documents
        return generate_documents(intake, score, self.manifest, strategic_analysis)

    def build_agent(self, *, tools: list | None = None, include_make_mcp: bool = False, include_gcp_mcp: bool = False):
        from .adk import build_agent
        return build_agent(self, tools=tools, include_make_mcp=include_make_mcp, include_gcp_mcp=include_gcp_mcp)

    def evaluate_policy(self, question: str, egg_source: Any = None) -> dict:
        from .core import evaluate_policy
        return evaluate_policy(question, egg_source)

    def parse_intake_tool(self, questionnaire_markdown: str) -> dict:
        """Parse a discovery intake questionnaire into structured fields."""
        return self.parse_questionnaire_markdown(questionnaire_markdown).__dict__

    def validate_intake_fields_tool(self, questionnaire_markdown: str) -> list[dict]:
        """Return missing or risky fields from a discovery intake questionnaire."""
        intake = self.parse_questionnaire_markdown(questionnaire_markdown)
        return [issue.__dict__ for issue in self.validate_intake(intake)]

    def score_client_opportunity_tool(self, questionnaire_markdown: str) -> dict:
        """Score a parsed intake and recommend the project tier."""
        intake = self.parse_questionnaire_markdown(questionnaire_markdown)
        return self.score_opportunity(intake).__dict__

    def generate_intake_documents_tool(self, questionnaire_markdown: str) -> dict:
        """Generate profile, opportunity analysis, and proposal draft markdown."""
        intake = self.parse_questionnaire_markdown(questionnaire_markdown)
        score = self.score_opportunity(intake)
        return self.generate_documents(intake, score)


def load_domain(manifest_path: str | Path) -> DomainEngine:
    manifest = DomainManifest.from_yaml(manifest_path)
    return DomainEngine(manifest)
