import os
os.environ["TESTING"] = "true"
import sys
# Dynamically add the local virtual environment's site-packages, project root, and tests/e2e to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.dirname(__file__))
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

from unittest.mock import MagicMock
# Dynamically stub any missing libraries to ensure pytest can import and run the test files
# even if some dependencies are not yet installed in the current environment.
for lib in ["requests", "obsws_python", "obswebsocket", "google.adk", "google.adk.agents", "google.adk.agents.llm_agent"]:
    try:
        __import__(lib)
    except ImportError:
        # Create a mock module structure
        parts = lib.split(".")
        for i in range(1, len(parts) + 1):
            parent = ".".join(parts[:i])
            if parent not in sys.modules:
                sys.modules[parent] = MagicMock()

# If google.adk was imported, ensure LlmAgent has a dummy run method so mock patch doesn't fail
try:
    import google.adk.agents.llm_agent
    if hasattr(google.adk.agents.llm_agent, "LlmAgent") and not hasattr(google.adk.agents.llm_agent.LlmAgent, "run"):
        google.adk.agents.llm_agent.LlmAgent.run = lambda self, *args, **kwargs: MagicMock()
except Exception:
    pass

# Stub out standard exceptions in requests if requests was stubbed
if isinstance(sys.modules.get("requests"), MagicMock):
    class MockTimeout(Exception):
        pass
    class MockRequestException(Exception):
        pass
    sys.modules["requests"].exceptions = MagicMock()
    sys.modules["requests"].exceptions.Timeout = MockTimeout
    sys.modules["requests"].exceptions.RequestException = MockRequestException

import http.client
import json
import os
import time
import socket
import threading
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Tuple, Union, Generator
import pytest
from unittest.mock import patch

from app import ClientDiscoveryHandler

FIXTURES_DIR = Path(__file__).parent / "fixtures"

class OpaqueBoxClient:
    """
    Lightweight, dependency-free HTTP client wrapper for E2E tests.
    Enforces strict black-box validation by routing all requests through http.client.
    """
    def __init__(self, base_url: str):
        self.base_url = base_url
        parsed = urlparse(base_url)
        self.host = parsed.netloc
        self.port = parsed.port

    def request(
        self, 
        method: str, 
        path: str, 
        json_data: Any = None, 
        headers: Dict[str, str] = None
    ) -> Tuple[int, Union[Dict[str, Any], str]]:
        headers = headers or {}
        body_bytes = None
        
        if json_data is not None:
            body_bytes = json.dumps(json_data).encode("utf-8")
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"
        
        # Connect to the HTTP Server
        conn = http.client.HTTPConnection(self.host)
        try:
            conn.request(method, path, body=body_bytes, headers=headers)
            response = conn.getresponse()
            status_code = response.status
            raw_body = response.read().decode("utf-8")
            
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed_body = raw_body
                
            return status_code, parsed_body
        finally:
            conn.close()


@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    """Starts the ThreadingHTTPServer from app.py in a background thread of the same Python process."""
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    # Wait for server to start up
    time.sleep(0.1)
    
    yield url
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def client(server_url: str) -> OpaqueBoxClient:
    """Provides an instance of OpaqueBoxClient configured to hit the server."""
    return OpaqueBoxClient(server_url)


@pytest.fixture
def load_questionnaire():
    """Fixture to load questionnaire markdown from e2e fixtures."""
    def _load(name: str) -> str:
        file_path = FIXTURES_DIR / "inputs" / f"{name}.md"
        if not file_path.exists():
            # Try inputs directory if it's named without .md extension
            file_path = FIXTURES_DIR / "inputs" / name
            if not file_path.exists() and not name.endswith(".md"):
                file_path = FIXTURES_DIR / "inputs" / f"{name}.md"
        return file_path.read_text(encoding="utf-8")
    return _load


@pytest.fixture
def load_response():
    """Fixture to load response json from e2e fixtures."""
    def _load(name: str) -> str:
        file_path = FIXTURES_DIR / "responses" / f"{name}.json"
        if not file_path.exists():
            file_path = FIXTURES_DIR / "responses" / name
        return file_path.read_text(encoding="utf-8")
    return _load


@pytest.fixture
def mock_gemini():
    """Mocks LlmAgent.run to return a standard strategic analysis."""
    # Since google-adk is imported, we can patch LlmAgent.run.
    # Note: If it's a stub, this will patch the stubbed version.
    with patch("google.adk.agents.llm_agent.LlmAgent.run") as mock_run, \
         patch.dict(os.environ, {"GEMINI_API_KEY": "valid_gemini_key"}):
        mock_response = MagicMock()
        mock_response.text = "## DIA Agent Strategic Analysis\n- Qualified Custom AI Agent opportunity.\n- High readiness score."
        mock_run.return_value = mock_response
        yield mock_run


@pytest.fixture
def mock_jules():
    """Mocks requests.post for the Jules REST API."""
    with patch("requests.post") as mock_post:
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"refined_content": "[Refined Enterprise Content]\nMocked refined strategic analysis by Jules."}
        mock_res.text = json.dumps({"refined_content": "[Refined Enterprise Content]\nMocked refined strategic analysis by Jules."})
        mock_post.return_value = mock_res
        yield mock_post


@pytest.fixture
def mock_obs():
    """Mocks both obsws_python.ReqClient and obswebsocket.obsws OBS clients."""
    with patch("obsws_python.ReqClient") as mock_req, patch("obswebsocket.obsws") as mock_ws:
        req_client = MagicMock()
        mock_req.return_value = req_client
        req_client.save_source_screenshot.return_value = MagicMock()
        req_client.save_replay_buffer.return_value = MagicMock()
        
        ws_client = MagicMock()
        mock_ws.return_value = ws_client
        ws_client.connect.return_value = None
        ws_client.disconnect.return_value = None
        ws_client.call.return_value = MagicMock()
        
        yield {
            "req_class": mock_req,
            "req_client": req_client,
            "ws_class": mock_ws,
            "ws_client": ws_client
        }
