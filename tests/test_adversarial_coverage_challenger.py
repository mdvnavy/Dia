import sys
import os
import http.client
import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app import ClientDiscoveryHandler, ThreadingHTTPServer
from client_discovery.core import (
    parse_questionnaire_markdown,
    score_opportunity,
    _score_budget_fit,
    _score_tech_readiness,
)


@pytest.fixture(scope="module")
def running_server():
    """Starts the ThreadingHTTPServer in a background thread and returns the port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    # Active polling with a max duration of 5.0 seconds
    start_time = time.time()
    success = False
    while time.time() - start_time < 5.0:
        try:
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=0.1):
                success = True
                break
        except (socket.error, ConnectionRefusedError):
            time.sleep(0.05)
            
    if not success:
        time.sleep(1.0)
        
    yield f"127.0.0.1:{server.server_port}"
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_unsupported_http_methods(running_server):
    """Sending PUT, DELETE, PATCH, or OPTIONS to the server should return 501 or 405/404, not crash."""
    host, port = running_server.split(":")
    
    for method in ["PUT", "DELETE", "PATCH", "OPTIONS"]:
        conn = http.client.HTTPConnection(host, int(port))
        try:
            conn.request(method, "/api/process", body="{}")
            res = conn.getresponse()
            # BaseHTTPRequestHandler returns 501 for unhandled methods
            assert res.status in [501, 405, 404]
            res.read()
        finally:
            conn.close()


def test_invalid_utf8_payload(running_server):
    """Sending non-UTF-8 binary bytes should be caught and return 400 Bad Request instead of 500 or crash."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        # Invalid UTF-8 sequence
        body = b"\xff\xfe\xfd"
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body))
        }
        conn.request("POST", "/api/process", body=body, headers=headers)
        res = conn.getresponse()
        assert res.status == 400
        response_body = json.loads(res.read().decode("utf-8"))
        assert "error" in response_body
    finally:
        conn.close()


def test_duplicate_content_length(running_server):
    """Sending duplicate Content-Length headers should be handled gracefully."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Send duplicate Content-Length headers
        request = (
            "POST /api/process HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 10\r\n"
            "Content-Length: 20\r\n"
            "\r\n"
            '{"questionnaire": "x"}'
        )
        sock.sendall(request.encode("utf-8"))
        sock.settimeout(2.0)
        try:
            response_data = sock.recv(4096).decode("utf-8")
            assert "HTTP/" in response_data
        except socket.timeout:
            pytest.fail("Server hung on duplicate Content-Length headers")
    finally:
        sock.close()


def test_parser_fragile_section_headers():
    """Verify that missing dot in section headers does NOT leak content across sections."""
    markdown_content = """# Discovery Intake Questionnaire
## 1. Company Basics
| Question | Answer |
| Company name | Test Inc |
| Budget range | $500-$2,500 |
| Final decision-maker | Owner |
| Preferred start date | ASAP |

## 3. Pain Points
| Question | Answer |
| Pain | Heavy manual reporting |

## 4. Goals
| Question | Answer |
| Goal | Automate reports |

## 5 Timeline
| Question | Answer |
| Timeline info | 2 weeks |
"""
    intake = parse_questionnaire_markdown(markdown_content)
    assert "2 weeks" not in intake.goals


def test_parser_table_pipe_truncation():
    """Verify that answers containing extra pipes are NOT truncated by the parser."""
    markdown_content = """# Discovery Intake Questionnaire
## 1. Company Basics
| Question | Answer |
| Company name | Test Inc |
| Budget range | $500-$2,500 |
| Final decision-maker | Owner |
| Preferred start date | ASAP |

## 2. Current Tools
| Question | Answer |
| Tools | Slack | Jira | GitHub |
"""
    intake = parse_questionnaire_markdown(markdown_content)
    assert intake.tools == "Slack | Jira | GitHub"


def test_opportunity_score_budget_boundary():
    """Verify that budget scoring does not match huge budgets due to substring checks."""
    score_500k = _score_budget_fit("$500,000", "Quick Win")
    assert score_500k == 2
    
    score_100k = _score_budget_fit("$100000", "Custom AI Agent")
    assert score_100k == 2


def test_opportunity_score_tech_readiness_fuzzy():
    """Verify that vague/fuzzy tech owner answers do NOT lead to false positive high readiness score."""
    score_fuzzy = _score_tech_readiness("not yet", "Slack")
    assert score_fuzzy == 2


def test_slowloris_connection_timeout(running_server):
    """Verify that the server does not hang forever on incomplete header writes and times out."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Send incomplete headers, then do not close the socket
        sock.sendall(b"POST /api/process HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        
        # We set client timeout to 15.0 seconds. The server's timeout is 10 seconds,
        # so it should close the connection within that time.
        sock.settimeout(15.0)
        try:
            data = sock.recv(1024)
            # The server closing the connection returns b"" (empty bytes)
            assert data == b""
        except socket.timeout:
            pytest.fail("Server hung and did not time out within 15 seconds")
    finally:
        sock.close()
