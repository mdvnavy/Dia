import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

from client_discovery.core import (
    client_score_dict,
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
)
from client_discovery import memory
from client_discovery.agent_instruction import AGENT_INSTRUCTION
from vertex_config import is_vertex_configured, vertex_gemini

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
    return client_score_dict(score_opportunity(intake))  # no tier price band: the agent must not quote one


def generate_intake_documents(questionnaire_markdown: str) -> dict:
    """Generate profile, opportunity analysis, and proposal draft markdown."""
    intake = parse_questionnaire_markdown(questionnaire_markdown)
    score = score_opportunity(intake)
    documents = generate_documents(intake, score)
    # Record the completed run in client memory (no-op unless MEMORY_SHEET_ID
    # is set; never raises). This is the agent path's write trigger — the
    # deterministic /api/process path records in app.py.
    memory.record_engagement(
        intake, score, source="/api/agent", documents=sorted(documents)
    )
    return documents


def recall_client_history(company_name: str, website: str = "") -> dict:
    """Look up whether this company has been seen before and return its
    prior engagement history (client record + recent intake runs)."""
    return memory.recall_summary(company_name, website)


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
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
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
    logger.info("Make MCP toolbox enabled.")
    return [
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=url, headers=headers)
        )
    ]


def _gcp_mcp_toolset() -> list:
    """Connect the agent to a GCP-managed MCP server when one is configured.

    Google publishes managed MCP endpoints for core Cloud APIs (monitoring,
    logging, storage, ...) — the same servers registered in the project's
    Agent Registry and governable through its Agent Gateway. Auth is the
    local Google credential (ADC), not an API key. Disabled (returns [])
    when GCP_MCP_URL is unset so tests and keyless local runs need no
    network or gcloud setup.
    """
    url = os.environ.get("GCP_MCP_URL")
    if not url:
        logger.info("GCP_MCP_URL not set; running without GCP MCP tools.")
        return []
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
        import google.auth
        import google.auth.transport.requests
    except ImportError as error:
        logger.warning("GCP MCP tools disabled, dependency missing: %s", error)
        return []
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        # Tokens live ~1h; build_agent() runs per turn, so each fresh agent
        # gets a fresh token and long sessions never hold an expired one.
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception as error:
        logger.warning("GCP MCP tools disabled, ADC unavailable: %s", error)
        return []
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {credentials.token}",
    }
    logger.info("GCP MCP tools enabled.")
    return [
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=url, headers=headers)
        )
    ]


# gemini-2.5-flash was retired on Vertex AI on 2026-10-20; gemini-3.8-flash is the
# listed replacement (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions).
# On Vertex it is served from the "global" location (and the us / eu multi-regions),
# not us-central1. DIA_MODEL still overrides it without a code change.
DEFAULT_MODEL = "gemini-3.8-flash"


def agent_model() -> str:
    """The model id for Ask DIA: DIA_MODEL when set (non-blank), else DEFAULT_MODEL."""
    return os.environ.get("DIA_MODEL", "").strip() or DEFAULT_MODEL


def build_agent(
    *, include_make_mcp: bool = False, include_gcp_mcp: bool = False
) -> LlmAgent:
    """Construct a fresh DIA agent.

    MCP toolsets bind to the event loop they first connect on, so callers that
    run each turn in its own asyncio.run() loop (like agent_runtime) must build
    a fresh agent per turn rather than sharing the module-level root_agent.
    """
    tools = [
        parse_intake,
        validate_intake_fields,
        score_client_opportunity,
        generate_intake_documents,
    ]
    # Plain-function tool, not MCP (spec Open Question #6): auto-included when
    # MEMORY_SHEET_ID is set, same present-means-on pattern as the MCP URLs.
    # Cheap to include unconditionally per turn — it's a local function, not a
    # network toolset, so it needs no per-message keyword routing.
    if memory.is_enabled():
        tools.append(recall_client_history)
    if include_make_mcp:
        tools.extend(_make_mcp_toolset())
    if include_gcp_mcp:
        tools.extend(_gcp_mcp_toolset())

    return LlmAgent(
        # Overridable so deploys can pin a model and local testing can dodge
        # per-model free-tier quotas without a code change.
        # Always a Vertex AI client (house rule): never the API-key endpoint.
        model=vertex_gemini(agent_model()),
        name="dia_discovery_intake_agent",
        instruction=AGENT_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=1.0,
                )
            )
        ),
        tools=tools,
    )


# Module-level agent for ADK CLI / single-loop consumers (e.g. `adk run`).
root_agent = build_agent()

if not is_vertex_configured():
    logger.warning(
        "Vertex AI is not configured (GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, "
        "GOOGLE_CLOUD_LOCATION); ADK chat runs will fail until it is. API keys are not used."
    )
