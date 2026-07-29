from __future__ import annotations

import logging
import requests
from client_discovery.config import load_config

logger = logging.getLogger(__name__)


def refine_with_jules(draft_content: str, target_persona: str) -> str:
    config = load_config()
    jules_api_key = config.get("JULES_API_KEY")
    if not jules_api_key or not str(jules_api_key).strip():
        logger.warning("JULES_API_KEY is not set or empty. Skipping Jules refinement.")
        return draft_content

    url = "https://jules.google/api/v1/refine"
    headers = {
        "Authorization": f"Bearer {jules_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "task": "evaluate_and_refine",
        "context": f"Target Audience: {target_persona}. Ensure strict enterprise tone.",
        "content": draft_content,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Jules API returned status code {response.status_code}. Falling back to original content.")
            return draft_content
        
        try:
            data = response.json()
        except ValueError:
            logger.warning("Jules API response was not valid JSON. Falling back to original content.")
            return draft_content

        if not isinstance(data, dict) or "refined_content" not in data:
            logger.warning("Jules API response does not contain 'refined_content' key. Falling back to original content.")
            return draft_content

        return data["refined_content"]

    except requests.exceptions.Timeout as e:
        logger.warning(f"Jules API request timed out: {e}. Falling back to original content.")
        return draft_content
    except requests.exceptions.RequestException as e:
        logger.warning(f"Jules API request failed: {e}. Falling back to original content.")
        return draft_content
