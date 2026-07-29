import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.llm_agent import LlmAgent

from client_discovery.core import domain_engine
from dia_engine.adk import _gcp_mcp_toolset, _make_mcp_toolset

REPO_ROOT = Path(__file__).resolve().parent

load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(override=False)

logger = logging.getLogger(__name__)


def parse_intake(questionnaire_markdown: str) -> dict:
    """Parse a discovery intake questionnaire into structured fields."""
    return domain_engine.parse_intake_tool(questionnaire_markdown)


def validate_intake_fields(questionnaire_markdown: str) -> list[dict]:
    """Return missing or risky fields from a discovery intake questionnaire."""
    return domain_engine.validate_intake_fields_tool(questionnaire_markdown)


def score_client_opportunity(questionnaire_markdown: str) -> dict:
    """Score a parsed intake and recommend the project tier."""
    return domain_engine.score_client_opportunity_tool(questionnaire_markdown)


def generate_intake_documents(questionnaire_markdown: str) -> dict:
    """Generate profile, opportunity analysis, and proposal draft markdown."""
    return domain_engine.generate_intake_documents_tool(questionnaire_markdown)


def build_agent(
    *, include_make_mcp: bool = False, include_gcp_mcp: bool = False
) -> LlmAgent:
    """Construct a fresh DIA agent."""
    tools = [
        parse_intake,
        validate_intake_fields,
        score_client_opportunity,
        generate_intake_documents,
    ]
    return domain_engine.build_agent(
        tools=tools, include_make_mcp=include_make_mcp, include_gcp_mcp=include_gcp_mcp
    )


root_agent = build_agent()

if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
    logger.warning(
        "GEMINI_API_KEY/GOOGLE_API_KEY is not set; ADK chat runs will fail until configured."
    )
