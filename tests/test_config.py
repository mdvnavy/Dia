import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

from unittest.mock import patch
import pytest

from client_discovery.config import load_and_validate_config

@pytest.fixture(autouse=True)
def reset_dotenv_loaded():
    import client_discovery.config
    client_discovery.config._dotenv_loaded = False
    yield
    client_discovery.config._dotenv_loaded = False

def test_load_config_success():
    env = {
        "GEMINI_API_KEY": "test-gemini-key",
        "JULES_API_KEY": "test-jules-key",
        "SERVER_IP": "192.168.1.100",
        "SERVER_PORT": "4444",
        "SERVER_PASSWORD": "secret-obs-pass",
        "TESTING": "false",
    }
    with patch.dict(os.environ, env, clear=True), patch("client_discovery.config.load_dotenv") as mock_load_dotenv:
        config = load_and_validate_config()
        assert config["GEMINI_API_KEY"] == "test-gemini-key"
        assert config["JULES_API_KEY"] == "test-jules-key"
        assert config["SERVER_IP"] == "192.168.1.100"
        assert config["SERVER_PORT"] == 4444
        assert config["SERVER_PASSWORD"] == "secret-obs-pass"
        mock_load_dotenv.assert_called()

def test_load_config_missing_gemini_key_in_production():
    env = {
        "TESTING": "false",
    }
    with patch.dict(os.environ, env, clear=True), patch("client_discovery.config.load_dotenv"):
        with pytest.raises(ValueError) as excinfo:
            load_and_validate_config()
        assert "GEMINI_API_KEY is required" in str(excinfo.value)

def test_load_config_missing_gemini_key_in_testing():
    env = {
        "TESTING": "true",
    }
    with patch.dict(os.environ, env, clear=True), patch("client_discovery.config.load_dotenv"), patch("client_discovery.config.logger") as mock_logger:
        config = load_and_validate_config()
        assert config["GEMINI_API_KEY"] is None
        mock_logger.warning.assert_any_call("GEMINI_API_KEY is not set.")

def test_load_config_missing_optional_keys():
    env = {
        "GEMINI_API_KEY": "test-gemini-key",
        "TESTING": "false",
    }
    with patch.dict(os.environ, env, clear=True), patch("client_discovery.config.load_dotenv"), patch("client_discovery.config.logger") as mock_logger:
        config = load_and_validate_config()
        assert config["GEMINI_API_KEY"] == "test-gemini-key"
        assert config["JULES_API_KEY"] is None
        assert config["SERVER_IP"] == "127.0.0.1"
        assert config["SERVER_PORT"] == 4455
        assert config["SERVER_PASSWORD"] is None
        mock_logger.warning.assert_any_call("JULES_API_KEY is not set. Jules refinement will fall back to returning original drafts.")

def test_load_config_invalid_port():
    env = {
        "GEMINI_API_KEY": "test-gemini-key",
        "SERVER_PORT": "invalid-port",
        "TESTING": "false",
    }
    with patch.dict(os.environ, env, clear=True), patch("client_discovery.config.load_dotenv"), patch("client_discovery.config.logger") as mock_logger:
        config = load_and_validate_config()
        assert config["SERVER_PORT"] == 4455
        mock_logger.warning.assert_any_call(
            "SERVER_PORT is set to 'invalid-port', which is not a valid integer. Falling back to 4455."
        )
