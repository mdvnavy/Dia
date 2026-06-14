import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

from pathlib import Path
from unittest.mock import patch
import pytest
import requests


from client_discovery.core import (
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
    refine_with_jules,
    trigger_obs_screenshot,
    save_obs_replay_buffer,
)


FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_questionnaire_extracts_company_profile_and_signals():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))

    assert intake.company_name == "Northstar Studio"
    assert intake.website == "https://northstar.example"
    assert intake.industry == "B2B services"
    assert intake.size == "12"
    assert intake.tools == "Google Workspace, HubSpot, Slack"
    assert len(intake.pain_points) == 2
    assert len(intake.goals) == 2
    assert intake.decision_maker == "Founder"


def test_validate_intake_flags_missing_submission_critical_fields():
    intake = parse_questionnaire_markdown(read_fixture("missing_fields_questionnaire.md"))

    issues = validate_intake(intake)
    issue_codes = {issue.code for issue in issues}

    assert {"missing_budget", "missing_decision_maker", "missing_start_date"} <= issue_codes
    assert all(issue.severity in {"warning", "error"} for issue in issues)


def test_score_opportunity_returns_tier_and_transparent_reasons():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))

    score = score_opportunity(intake)

    assert score.tier == "Custom AI Agent"
    assert score.total_score == 12
    assert score.max_score == 20
    assert score.price_range == "$2,500-$10,000"
    assert any("2 pain point" in reason for reason in score.reasons)


def test_generate_documents_returns_public_safe_markdown_outputs():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))
    score = score_opportunity(intake)

    documents = generate_documents(intake, score)

    assert set(documents) == {
        "client-profile.md",
        "opportunity-analysis.md",
        "proposal-draft.md",
    }
    combined = "\n".join(documents.values())
    assert "Northstar Studio" in combined
    assert "Custom AI Agent" in combined
    assert "billin" not in combined.lower()


def test_refine_with_jules_success():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_response = mock_post.return_value
        mock_response.status_code = 200
        mock_response.json.return_value = {"refined_content": "Refined Draft"}
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Refined Draft"
        
        mock_post.assert_called_once()
        call_args, call_kwargs = mock_post.call_args
        assert call_args[0] == "https://jules.google/api/v1/refine"
        assert call_kwargs["headers"]["Authorization"] == "Bearer valid_key"
        assert call_kwargs["json"]["content"] == "Original Draft"
        assert "CEO" in call_kwargs["json"]["context"]


def test_refine_with_jules_missing_key():
    env = {"JULES_API_KEY": ""}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"
        mock_post.assert_not_called()


def test_refine_with_jules_status_error():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_response = mock_post.return_value
        mock_response.status_code = 500
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"


def test_refine_with_jules_timeout():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"


def test_refine_with_jules_request_exception():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.RequestException("Request error")
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"


def test_refine_with_jules_malformed_json():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_response = mock_post.return_value
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Malformed JSON")
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"


def test_refine_with_jules_missing_refined_content_key():
    env = {"JULES_API_KEY": "valid_key"}
    with patch.dict(os.environ, env), patch("requests.post") as mock_post:
        mock_response = mock_post.return_value
        mock_response.status_code = 200
        mock_response.json.return_value = {"something_else": "here"}
        
        result = refine_with_jules("Original Draft", "CEO")
        assert result == "Original Draft"


def test_trigger_obs_screenshot_missing_credentials():
    env = {"OBS_HOST": "", "OBS_PORT": "", "OBS_PASSWORD": ""}
    with patch.dict(os.environ, env):
        res = trigger_obs_screenshot()
        assert "skipped" in res.lower() or "missing" in res.lower()


def test_save_obs_replay_buffer_missing_credentials():
    env = {"OBS_HOST": "", "OBS_PORT": "", "OBS_PASSWORD": ""}
    with patch.dict(os.environ, env):
        res = save_obs_replay_buffer()
        assert "skipped" in res.lower() or "missing" in res.lower()


def test_trigger_obs_screenshot_success():
    env = {"OBS_HOST": "localhost", "OBS_PORT": "4455", "OBS_PASSWORD": "pass"}
    with patch.dict(os.environ, env), patch("obsws_python.ReqClient") as mock_req:
        mock_client = mock_req.return_value
        res = trigger_obs_screenshot(file_path="test_shot.png")
        assert "saved" in res.lower() or "success" in res.lower()
        # Matches the real obsws-python ReqClient.save_source_screenshot
        # signature: (name, img_format, file_path, width, height, quality).
        mock_client.save_source_screenshot.assert_called_once_with(
            "Display Capture", "png", "test_shot.png", 1920, 1080, -1
        )


def test_save_obs_replay_buffer_success():
    env = {"OBS_HOST": "localhost", "OBS_PORT": "4455", "OBS_PASSWORD": "pass"}
    with patch.dict(os.environ, env), patch("obsws_python.ReqClient") as mock_req:
        mock_client = mock_req.return_value
        res = save_obs_replay_buffer()
        assert "saved" in res.lower() or "success" in res.lower()
        mock_client.save_replay_buffer.assert_called_once()
