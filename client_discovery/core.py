from __future__ import annotations

from pathlib import Path
from dia_engine import load_domain, refine_with_jules, save_obs_replay_buffer, trigger_obs_screenshot
from dia_engine.core import (
    _bullet_lines,
    _extract_section_answers,
    _extract_table_rows,
    _normalize,
    _numbered_lines,
)
from dia_engine.obs import _resolve_obs_credentials
from dia_engine.scoring import _score_budget_fit, _score_tech_readiness
from .models import ClientIntake, OpportunityScore, ValidationIssue

_MANIFEST_PATH = Path(__file__).parent / "domain_manifest.yaml"
domain_engine = load_domain(_MANIFEST_PATH)

QUESTION_ALIASES = domain_engine.manifest.question_aliases


def parse_questionnaire_markdown(content: str) -> ClientIntake:
    return domain_engine.parse_questionnaire_markdown(content)


def validate_intake(intake: ClientIntake) -> list[ValidationIssue]:
    return domain_engine.validate_intake(intake)


def score_opportunity(intake: ClientIntake) -> OpportunityScore:
    return domain_engine.score_opportunity(intake)


def generate_documents(
    intake: ClientIntake, score: OpportunityScore, strategic_analysis: str | None = None
) -> dict[str, str]:
    return domain_engine.generate_documents(intake, score, strategic_analysis)
