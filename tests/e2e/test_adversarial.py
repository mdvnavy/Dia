import os
import sys
import pytest
from unittest.mock import patch, MagicMock
import http.client
import json
import time
import subprocess
import signal
from urllib.parse import urlparse

# Ensure project root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app import PUBLIC_FILES


def test_malformed_json_payloads(server_url):
    """Test POST /api/process with malformed JSON payloads."""
    parsed = urlparse(server_url)
    host = parsed.netloc

    # 1. Completely invalid JSON
    conn = http.client.HTTPConnection(host)
    try:
        conn.request("POST", "/api/process", body="{invalid_json}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read().decode("utf-8"))
        assert "error" in body
        assert "json" in body["error"].lower()
    finally:
        conn.close()

    # 2. JSON array instead of dict object
    conn = http.client.HTTPConnection(host)
    try:
        conn.request("POST", "/api/process", body="[1, 2, 3]", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read().decode("utf-8"))
        assert "error" in body
        assert "object" in body["error"].lower()
    finally:
        conn.close()

    # 3. JSON string instead of dict object
    conn = http.client.HTTPConnection(host)
    try:
        conn.request("POST", "/api/process", body='"just a string"', headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read().decode("utf-8"))
        assert "error" in body
        assert "object" in body["error"].lower()
    finally:
        conn.close()

    # 4. Missing questionnaire key (empty payload)
    conn = http.client.HTTPConnection(host)
    try:
        conn.request("POST", "/api/process", body="{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 400
        body = json.loads(resp.read().decode("utf-8"))
        assert "error" in body
        assert "required" in body["error"].lower()
    finally:
        conn.close()


def test_malformed_content_length(server_url):
    """Test requests with invalid Content-Length headers (e.g. non-integer values)."""
    parsed = urlparse(server_url)
    host = parsed.netloc

    conn = http.client.HTTPConnection(host)
    try:
        # If Content-Length is invalid (non-integer), it should fall back to 0
        conn.request(
            "POST",
            "/api/process",
            body='{"questionnaire": "some content"}',
            headers={"Content-Type": "application/json", "Content-Length": "invalid_length"}
        )
        resp = conn.getresponse()
        # With content_length defaulted to 0, read_json returns {} which triggers questionnaire is required -> 400
        assert resp.status == 400
        body = json.loads(resp.read().decode("utf-8"))
        assert "error" in body
        assert "required" in body["error"].lower()
    finally:
        conn.close()


def test_negative_content_length(server_url):
    """Test requests with negative Content-Length values."""
    parsed = urlparse(server_url)
    host = parsed.netloc

    conn = http.client.HTTPConnection(host, timeout=2.0)
    try:
        # Sending negative Content-Length
        conn.request(
            "POST",
            "/api/process",
            body='{"questionnaire": "some content"}',
            headers={"Content-Type": "application/json", "Content-Length": "-10"}
        )
        resp = conn.getresponse()
        # We expect the server to handle this gracefully and not block/timeout.
        # If it doesn't block, it will read body or return 400.
        assert resp.status in (400, 500)
    except (socket.timeout, TimeoutError) as e:
        pytest.fail(f"Server hung/blocked on negative Content-Length: {e}")
    finally:
        conn.close()


def test_extremely_large_content_length(server_url):
    """Test requests with extremely large Content-Length values to verify OOM/hang resilience."""
    parsed = urlparse(server_url)
    host = parsed.netloc

    conn = http.client.HTTPConnection(host, timeout=2.0)
    try:
        # Sending extremely large Content-Length (1 TB)
        conn.request(
            "POST",
            "/api/process",
            body='{"questionnaire": "some content"}',
            headers={"Content-Type": "application/json", "Content-Length": "1000000000000"}
        )
        resp = conn.getresponse()
        # We expect the server to handle this gracefully (e.g. reject or timeout, but not OOM/crash).
        assert resp.status in (400, 413, 500)
    except (socket.timeout, TimeoutError) as e:
        # A timeout indicates the server hung or is reading indefinitely
        pytest.fail(f"Server hung/blocked on extremely large Content-Length: {e}")
    finally:
        conn.close()


def test_missing_static_files(server_url):
    """Test requesting static files when they are missing on disk."""
    parsed = urlparse(server_url)
    host = parsed.netloc

    # Patch PUBLIC_FILES to point to a non-existent file for a specific path
    original_path = PUBLIC_FILES["/static/style.css"]
    PUBLIC_FILES["/static/style.css"] = original_path.parent / "non_existent_file.css"

    try:
        conn = http.client.HTTPConnection(host)
        conn.request("GET", "/static/style.css")
        resp = conn.getresponse()
        assert resp.status == 404
        body = json.loads(resp.read().decode("utf-8"))
        assert body == {"error": "not found"}
    finally:
        # Restore original path
        PUBLIC_FILES["/static/style.css"] = original_path


def test_keyboard_interrupt_graceful_shutdown():
    """Test that KeyboardInterrupt gracefully stops the server, prints logs, and closes sockets."""
    import app

    # Create a mock server instance
    mock_server = MagicMock()
    # Configure serve_forever to raise KeyboardInterrupt to simulate Ctrl+C
    mock_server.serve_forever.side_effect = KeyboardInterrupt()

    # Patch ThreadingHTTPServer and print to verify behavior
    with patch("app.ThreadingHTTPServer", return_value=mock_server), \
         patch("builtins.print") as mock_print:
        
        app.run_server()
        
        # Verify serve_forever was called
        mock_server.serve_forever.assert_called_once()
        # Verify server_close was called in the finally block
        mock_server.server_close.assert_called_once()
        
        # Verify expected print statements were made
        printed_messages = [call[0][0] for call in mock_print.call_args_list if call[0]]
        assert any("KeyboardInterrupt received" in msg for msg in printed_messages), \
            f"Expected KeyboardInterrupt log missing from: {printed_messages}"
        assert any("Server socket closed" in msg for msg in printed_messages), \
            f"Expected server socket closed log missing from: {printed_messages}"



