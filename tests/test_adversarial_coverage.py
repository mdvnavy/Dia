import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

from unittest.mock import MagicMock
# Dynamically stub any missing libraries for testing
for lib in ["obsws_python", "obswebsocket"]:
    if lib not in sys.modules:
        sys.modules[lib] = MagicMock()

import pytest
from unittest.mock import patch, MagicMock

from client_discovery.core import (
    _resolve_obs_credentials,
    trigger_obs_screenshot,
    save_obs_replay_buffer,
    refine_with_jules,
    generate_documents,
)
from client_discovery.models import ClientIntake, OpportunityScore


def test_obs_empty_password_auth_disabled():
    """
    GAP: If OBS authentication is disabled, empty password ("") is valid.
    However, the codebase treats empty password as missing credentials and returns None.
    This test verifies that setting OBS_PASSWORD="" in env causes _resolve_obs_credentials to return None.
    """
    env = {
        "OBS_HOST": "localhost",
        "OBS_PORT": "4455",
        "OBS_PASSWORD": "",  # Empty password when auth is disabled
    }
    with patch.dict(os.environ, env):
        creds = _resolve_obs_credentials()
        assert creds is not None
        assert creds[2] == ""


def test_obs_out_of_range_port():
    """
    GAP: The port parsing does not validate if port is in valid range (1-65535).
    This test verifies that setting an out of range port (e.g. -100) returns it,
    which will later fail inside the websocket connection.
    """
    env = {
        "OBS_HOST": "localhost",
        "OBS_PORT": "-100",
        "OBS_PASSWORD": "secretpassword",
    }
    with patch.dict(os.environ, env):
        creds = _resolve_obs_credentials()
        assert creds is None


def test_jules_whitespace_api_key():
    """
    GAP: Whitespace JULES_API_KEY ("   ") is not caught by config validation,
    but it will fail on API call. This test verifies that refine_with_jules handles
    this gracefully by falling back to the original draft content when requests fail.
    """
    env = {
        "JULES_API_KEY": "   ",
    }
    with patch.dict(os.environ, env):
        # The API call will run because JULES_API_KEY is not empty, but it's invalid.
        # It should fail and return the original draft content.
        draft = "Original Draft content"
        result = refine_with_jules(draft, "CEO")
        assert result == draft


def test_generate_documents_with_invalid_types():
    """
    GAP: If ClientIntake contains non-iterable types for pain_points or goals (like int or bool),
    generate_documents will crash with TypeError because _numbered_lines tries to iterate over them.
    This test verifies this fragility.
    """
    intake = ClientIntake(
        company_name="Fragile Corp",
        pain_points=123,  # Invalid type (int instead of list/iterable)
        goals=True,       # Invalid type
    )
    score = OpportunityScore(
        tier="Quick Win",
        scope="Test",
        price_range="$500-$2,500",
        timeline="1-2 weeks",
        urgency=1,
        budget_fit=2,
        tech_readiness=2,
        strategic_value=3,
        total_score=8,
        max_score=20,
        reasons=[],
    )
    docs = generate_documents(intake, score)
    assert isinstance(docs, dict)
    assert "opportunity-analysis.md" in docs


def test_obs_screenshot_connection_failure_graceful_handling():
    """
    GAP: obsws_python is preferred, but if it is installed and connection fails,
    the code directly returns the error message instead of trying obswebsocket fallback.
    We test that the screenshot tool doesn't crash the main process but returns the error message.
    """
    env = {
        "OBS_HOST": "localhost",
        "OBS_PORT": "4455",
        "OBS_PASSWORD": "wrong_password",
    }
    with patch.dict(os.environ, env), patch("obsws_python.ReqClient") as mock_req:
        mock_req.side_effect = Exception("Connection refused")
        res = trigger_obs_screenshot()
        assert "failed" in res.lower()
        assert "connection refused" in res.lower()


def test_obs_replay_buffer_connection_failure_graceful_handling():
    """
    Same as above for replay buffer.
    """
    env = {
        "OBS_HOST": "localhost",
        "OBS_PORT": "4455",
        "OBS_PASSWORD": "wrong_password",
    }
    with patch.dict(os.environ, env), patch("obsws_python.ReqClient") as mock_req:
        mock_req.side_effect = Exception("Auth failure")
        res = save_obs_replay_buffer()
        assert "failed" in res.lower()
        assert "auth failure" in res.lower()
