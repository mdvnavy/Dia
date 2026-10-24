"""Synchronous bridge between the stdlib HTTP demo and the ADK Gemini agent.

The web demo runs on Python's threaded ``http.server``, while ADK exposes an
async ``Runner``. This module wraps a single agent turn in ``asyncio.run`` so a
request handler can call :func:`run_agent` synchronously, and degrades
gracefully when Vertex AI is not configured. Gemini is reached only through
Vertex AI (see ``vertex_config``); an API key in the environment is ignored.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Match character.py: pick up a repo-local .env so local demos see the key,
# while real environment variables (Cloud Run / Codespaces secrets) always
# win because override stays False.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
load_dotenv(override=False)

APP_NAME = "dia_discovery_intake_agent"
USER_ID = "demo-user"
logger = logging.getLogger(__name__)

MCP_MODES = {
    "none": (False, False),
    "make": (True, False),
    "gcp": (False, True),
    "all": (True, True),
}

class AgentNotConfigured(RuntimeError):
    """Raised when Vertex AI is not configured for live agent runs."""


def is_configured() -> bool:
    """Return True when Vertex AI is configured (API keys never count)."""
    from vertex_config import is_vertex_configured

    return is_vertex_configured()


def _select_mcp_toolsets(message: str = "") -> tuple[bool, bool]:
    """Return (include_make, include_gcp) from explicit configuration only.

    MCP toolsets can trigger real side effects (Make scenarios) or attach very
    large schemas (GCP Monitoring), so they are never switched on by words in
    the user's message - that would let any prompt (or prompt injection) opt
    itself into tools. ``DIA_AGENT_MCP_MODE`` is the only switch:

    ``none`` (default) | ``make`` | ``gcp`` | ``all``

    Unknown values (including the retired ``auto``) fall back to ``none``.
    ``message`` is accepted for call-site compatibility and deliberately unused.
    """
    del message
    mode = os.environ.get("DIA_AGENT_MCP_MODE", "none").strip().lower() or "none"
    if mode not in MCP_MODES:
        logger.warning("Unknown DIA_AGENT_MCP_MODE %r; MCP tools disabled", mode)
        mode = "none"
    return MCP_MODES[mode]


async def _run_turn(message: str) -> str:
    # Imported lazily so the deterministic endpoints and tests do not require
    # the heavy ADK/genai stack to be importable.
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from character import build_agent

    # Each turn runs in its own asyncio.run() loop, and MCP toolsets bind to
    # the loop they first connect on - so the agent (and its toolsets) must be
    # built fresh here rather than shared at module level.
    include_make_mcp, include_gcp_mcp = _select_mcp_toolsets(message)
    agent = build_agent(
        include_make_mcp=include_make_mcp, include_gcp_mcp=include_gcp_mcp
    )

    session_service = InMemorySessionService()
    session_id = uuid.uuid4().hex
    await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )

    runner = Runner(
        app_name=APP_NAME, agent=agent, session_service=session_service
    )
    new_message = types.Content(role="user", parts=[types.Part(text=message)])

    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=new_message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(part.text or "" for part in event.content.parts)
    finally:
        # Close MCP sessions inside this loop; the MCP client's cancel-scope
        # teardown can still grumble on close, but the turn result is already
        # captured, so never let cleanup noise break the response.
        try:
            from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

            for tool in agent.tools:
                if isinstance(tool, McpToolset):
                    await tool.close()
        except Exception:  # noqa: BLE001 - cleanup must never mask the reply
            logger.debug("MCP toolset cleanup raised; ignoring", exc_info=True)

    return final_text.strip()


def run_agent(message: str) -> str:
    """Run a single DIA agent turn and return its final text response.

    Raises:
        ValueError: when ``message`` is empty.
        AgentNotConfigured: when Vertex AI is not configured.
    """
    from vertex_config import VERTEX_ENV_HINT

    if not message.strip():
        raise ValueError("message is required")
    if not is_configured():
        raise AgentNotConfigured(f"{VERTEX_ENV_HINT} Live Gemini agent runs use Vertex AI only.")
    return asyncio.run(_run_turn(message))
