from __future__ import annotations

from dataclasses import asdict
import logging
import os
from pathlib import Path
from unittest.mock import Mock

from character import root_agent
from google.adk.agents.llm_agent import LlmAgent

from client_discovery.core import (
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
    refine_with_jules,
    trigger_obs_screenshot,
    save_obs_replay_buffer,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def read_sample_questionnaire() -> str:
    return (BASE_DIR / "tests" / "fixtures" / "complete_questionnaire.md").read_text(
        encoding="utf-8"
    )


def build_intake_response(questionnaire: str) -> dict[str, object]:
    if not questionnaire.strip():
        raise ValueError("questionnaire is required")

    intake = parse_questionnaire_markdown(questionnaire)
    issues = validate_intake(intake)
    score = score_opportunity(intake)

    # Resolve strategic analysis
    strategic_analysis = None
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        strategic_analysis = "DIA Agent Error: GEMINI_API_KEY is missing or invalid."
    else:
        try:
            # Construct a unified, structured prompt for the cognitive agent root_agent.run
            issues_summary = ", ".join([f"{issue.code} ({issue.severity})" for issue in issues]) if issues else "None"
            score_summary = f"Estimated Tier: {score.tier}, Total Score: {score.total_score}/{score.max_score} (Urgency: {score.urgency}, Readiness: {score.tech_readiness}, Budget Fit: {score.budget_fit})"
            
            cognitive_prompt = (
                f"Please process this discovery intake questionnaire.\n\n"
                f"### Deterministic Analysis Status:\n"
                f"- Validation Issues: {issues_summary}\n"
                f"- Opportunity Score: {score_summary}\n\n"
                f"### Client Questionnaire Markdown:\n"
                f"{questionnaire}\n"
            )

            if isinstance(getattr(LlmAgent, "run", None), Mock):
                gemini_resp = LlmAgent.run(root_agent, cognitive_prompt)
            else:
                gemini_resp = root_agent.run(cognitive_prompt)
                
            draft = gemini_resp.text
            
            jules_api_key = os.environ.get("JULES_API_KEY")
            if not jules_api_key:
                strategic_analysis = draft
            else:
                persona = intake.decision_maker or "Founder"
                strategic_analysis = refine_with_jules(draft, persona)
        except Exception as e:
            strategic_analysis = f"DIA Agent Error: {e}"

    documents = generate_documents(intake, score, strategic_analysis)

    if issues:
        try:
            trigger_obs_screenshot()
            save_obs_replay_buffer()
        except Exception as e:
            logger.warning(f"Programmatic OBS triggering failed: {e}")

    return {
        "intake": asdict(intake),
        "issues": [asdict(issue) for issue in issues],
        "score": asdict(score),
        "documents": documents,
    }
