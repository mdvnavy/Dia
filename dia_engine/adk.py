from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

if TYPE_CHECKING:
    from .schema import DomainEngine

logger = logging.getLogger(__name__)


def _make_mcp_toolset() -> list:
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


def build_agent(
    domain_engine: DomainEngine,
    *,
    tools: list | None = None,
    include_make_mcp: bool = False,
    include_gcp_mcp: bool = False,
) -> LlmAgent:
    if tools is None:
        tools = [
            domain_engine.parse_intake_tool,
            domain_engine.validate_intake_fields_tool,
            domain_engine.score_client_opportunity_tool,
            domain_engine.generate_intake_documents_tool,
        ]
    else:
        tools = list(tools)

    if include_make_mcp:
        tools.extend(_make_mcp_toolset())
    if include_gcp_mcp:
        tools.extend(_gcp_mcp_toolset())

    return LlmAgent(
        model=os.environ.get("DIA_MODEL", "gemini-2.5-flash"),
        name="dia_discovery_intake_agent",
        instruction=domain_engine.manifest.agent_instruction,
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
