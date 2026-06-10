import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

from client_discovery.core import (
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
)

REPO_ROOT = Path(__file__).resolve().parent

# Load a repo-local .env if present, then fall back to any .env discovered up
# the tree. Existing environment variables (e.g. Cloud Run / Codespaces
# secrets) always win because override stays False.
load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(override=False)

logger = logging.getLogger(__name__)


def parse_intake(questionnaire_markdown: str) -> dict:
    """Parse a discovery intake questionnaire into structured fields."""
    return parse_questionnaire_markdown(questionnaire_markdown).__dict__


def validate_intake_fields(questionnaire_markdown: str) -> list[dict]:
    """Return missing or risky fields from a discovery intake questionnaire."""
    intake = parse_questionnaire_markdown(questionnaire_markdown)
    return [issue.__dict__ for issue in validate_intake(intake)]


def score_client_opportunity(questionnaire_markdown: str) -> dict:
    """Score a parsed intake and recommend the project tier."""
    intake = parse_questionnaire_markdown(questionnaire_markdown)
    return score_opportunity(intake).__dict__


def generate_intake_documents(questionnaire_markdown: str) -> dict:
    """Generate profile, opportunity analysis, and proposal draft markdown."""
    intake = parse_questionnaire_markdown(questionnaire_markdown)
    score = score_opportunity(intake)
    return generate_documents(intake, score)


def _make_mcp_toolset() -> list:
    """Connect the agent to a Make MCP Toolbox when one is configured.

    The toolbox is a curated MCP server on the Make side: only the scenarios
    published into it are exposed as tools, so the agent never sees the rest
    of the Make account. Disabled (returns []) when MAKE_MCP_URL is unset so
    tests and keyless local runs need no network or Make account.
    """
    url = os.environ.get("MAKE_MCP_URL")
    if not url:
        logger.info("MAKE_MCP_URL not set; running without the Make MCP toolbox.")
        return []
    try:
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
    except ImportError as error:
        logger.warning("Make MCP toolbox disabled, ADK MCP support missing: %s", error)
        return []

    headers = {"Accept": "application/json, text/event-stream"}
    token = os.environ.get("MAKE_MCP_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    logger.info("Make MCP toolbox enabled at %s", url.split("?")[0])
    return [
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=url, headers=headers)
        )
    ]


root_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="dia_discovery_intake_agent",
    instruction="""
        You are DIA, the discovery intake agent for a startup services team.

        Mission:
        - Convert a founder or prospect questionnaire into a structured discovery profile.
        - Identify missing fields before a proposal is trusted.
        - Score the opportunity with transparent reasons.
        - Generate practical markdown outputs: client profile, opportunity analysis, and proposal draft.

        Operating rules:
        - Use only fictional or user-provided demo data.
        - Do not expose secrets, private client data, or unrelated internal product material.
        - Ask targeted follow-up questions when budget, decision maker, start date, goals, or pain points are missing.
        - Keep recommendations grounded in the scoring tool output.
        - When Make toolbox tools are available and an intake is qualified (or the
          user asks for follow-up actions), use them to execute the handoff, e.g.
          lead automation. Report exactly which tool ran and what it returned.
    """,
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=3,
                initial_delay=1.0,
            )
        )
    ),
    tools=[
        parse_intake,
        validate_intake_fields,
        score_client_opportunity,
        generate_intake_documents,
        *_make_mcp_toolset(),
    ],
)

if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
    logger.warning(
        "GEMINI_API_KEY/GOOGLE_API_KEY is not set; ADK chat runs will fail until configured."
    )
