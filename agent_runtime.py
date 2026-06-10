"""Synchronous bridge between the stdlib HTTP demo and the ADK Gemini agent.

The web demo runs on Python's threaded ``http.server``, while ADK exposes an
async ``Runner``. This module wraps a single agent turn in ``asyncio.run`` so a
request handler can call :func:`run_agent` synchronously, and degrades
gracefully when ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` is not configured.
"""

from __future__ import annotations

import asyncio
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


class AgentNotConfigured(RuntimeError):
    """Raised when no Google API key is available for live agent runs."""


def is_configured() -> bool:
    """Return True when a Gemini/Google API key is present in the environment."""
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


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
    agent = build_agent()

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
            from google.adk.tools.mcp_tool import McpToolset

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
        AgentNotConfigured: when no Google API key is configured.
    """
    if not message.strip():
        raise ValueError("message is required")
    if not is_configured():
        raise AgentNotConfigured(
            "Set GEMINI_API_KEY (or GOOGLE_API_KEY) to enable live Gemini agent runs."
        )
    return asyncio.run(_run_turn(message))
