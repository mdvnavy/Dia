"""Vertex AI is the only route DIA uses to reach Gemini.

House rule (same as Tia, ``apps/tia/app.py`` ``vertex_settings``): Google model
calls go through Vertex AI with Application Default Credentials, never the
API-key developer endpoint. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` are ignored
even when present: the client is always built with ``vertexai=True`` and an
explicit project/location, which google-genai documents as taking precedence
over an implicit API key from the environment.
"""

from __future__ import annotations

import os
from functools import cached_property
from typing import Any

VERTEX_ENV_HINT = (
    "Set GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_PROJECT and "
    "GOOGLE_CLOUD_LOCATION for Vertex AI."
)


class VertexNotConfigured(RuntimeError):
    """Vertex AI is not configured; DIA will not fall back to an API key."""


def vertex_settings() -> tuple[str, str]:
    """Return (project, location) for Vertex AI, or raise if Vertex is not configured."""
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() not in {"true", "1"}:
        raise VertexNotConfigured(
            "Vertex AI is not enabled: set GOOGLE_GENAI_USE_VERTEXAI=TRUE."
        )
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    if not project or not location:
        raise VertexNotConfigured(
            "Set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION for Vertex AI."
        )
    return project, location


def is_vertex_configured() -> bool:
    try:
        vertex_settings()
    except VertexNotConfigured:
        return False
    return True


def vertex_client_kwargs() -> dict[str, Any]:
    project, location = vertex_settings()
    return {"vertexai": True, "project": project, "location": location}


def vertex_gemini(model: str):
    """An ADK ``Gemini`` model whose client is always a Vertex AI client.

    The Vertex settings are resolved when the client is first needed (not at
    import), so a module-level agent can be built without env; any call made
    while Vertex is unconfigured raises ``VertexNotConfigured`` instead of
    quietly picking up an API key.
    """
    from google.adk.models.google_llm import Gemini

    class VertexGemini(Gemini):
        @cached_property
        def api_client(self):  # type: ignore[override]
            self.client_kwargs = {**(self.client_kwargs or {}), **vertex_client_kwargs()}
            client = Gemini.api_client.func(self)  # type: ignore[attr-defined]
            if not getattr(client, "vertexai", False) or getattr(
                getattr(client, "_api_client", None), "api_key", None
            ):
                raise VertexNotConfigured("Refusing a non-Vertex Gemini client.")
            return client

    return VertexGemini(model=model)
