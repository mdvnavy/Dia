from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_ENV_PATH = REPO_ROOT / ".env"


_dotenv_loaded = False


def load_and_validate_config() -> dict[str, Any]:
    """
    Locates and loads environment variables and returns
    a dictionary of configuration parameters.
    """
    global _dotenv_loaded
    # Load env files
    if not _dotenv_loaded:
        load_dotenv(REPO_ENV_PATH, override=False)
        _dotenv_loaded = True

    gemini_api_key = os.environ.get("GEMINI_API_KEY")

    # Optional variables
    jules_api_key = os.environ.get("JULES_API_KEY")
    if not jules_api_key:
        logger.warning(
            "JULES_API_KEY is not set. Jules refinement will fall back to returning original drafts."
        )

    server_ip = os.environ.get("SERVER_IP")
    server_port_str = os.environ.get("SERVER_PORT")
    server_password = os.environ.get("SERVER_PASSWORD")

    # Log missing OBS credentials
    missing_obs = []
    if not server_ip:
        missing_obs.append("SERVER_IP")
    if not server_port_str:
        missing_obs.append("SERVER_PORT")
    if not server_password:
        missing_obs.append("SERVER_PASSWORD")

    if missing_obs:
        logger.warning(
            f"Missing OBS credentials: {', '.join(missing_obs)}. "
            "Falling back to defaults (IP=\"127.0.0.1\", Port=4455)."
        )

    # Determine fallback values
    ip_val = server_ip if server_ip else "127.0.0.1"

    # Server port parsing and fallback
    if server_port_str:
        try:
            port_val = int(server_port_str)
        except ValueError:
            logger.warning(
                f"SERVER_PORT is set to '{server_port_str}', which is not a valid integer. "
                "Falling back to 4455."
            )
            port_val = 4455
    else:
        port_val = 4455

    return {
        "GEMINI_API_KEY": gemini_api_key,
        "JULES_API_KEY": jules_api_key,
        "SERVER_IP": ip_val,
        "SERVER_PORT": port_val,
        "SERVER_PASSWORD": server_password,
    }


def require_gemini_api_key(config: dict[str, Any] | None = None) -> str:
    """Returns a configured Gemini API key or raises if missing/empty."""
    resolved_config = config if config is not None else load_and_validate_config()
    gemini_api_key = str(resolved_config.get("GEMINI_API_KEY") or "").strip()
    if not gemini_api_key:
        raise ValueError("GEMINI_API_KEY is required but missing from the environment.")
    return gemini_api_key


# Provide load_config alias
load_config = load_and_validate_config
