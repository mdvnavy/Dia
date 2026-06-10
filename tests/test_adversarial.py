import sys
import os
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

import http.client
import json
import socket
import threading
import time
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app import ClientDiscoveryHandler, ThreadingHTTPServer, PUBLIC_FILES


@pytest.fixture
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
        time.sleep(1.0)  # Fallback to sleeping a bit more just in case
        
    yield f"127.0.0.1:{server.server_port}"
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_endpoint_correctness_get_valid(running_server):
    """GET endpoints / and static files return status 200 and correct format."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        # GET /
        conn.request("GET", "/")
        res = conn.getresponse()
        assert res.status == 200
        body = res.read().decode("utf-8")
        assert "<html" in body.lower() or "<!doctype html" in body.lower()
        
        # GET /static/app.js
        conn.request("GET", "/static/app.js")
        res = conn.getresponse()
        assert res.status == 200
        
        # GET /api/sample
        conn.request("GET", "/api/sample")
        res = conn.getresponse()
        assert res.status == 200
        body = json.loads(res.read().decode("utf-8"))
        assert "questionnaire" in body
    finally:
        conn.close()


def test_endpoint_correctness_not_found(running_server):
    """Endpoints that do not exist return 404 Not Found."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        # GET /api/invalid
        conn.request("GET", "/api/invalid")
        res = conn.getresponse()
        assert res.status == 404
        body = json.loads(res.read().decode("utf-8"))
        assert body == {"error": "not found"}
        
        # POST /api/invalid
        conn.request("POST", "/api/invalid", body="{}")
        res = conn.getresponse()
        assert res.status == 404
        body = json.loads(res.read().decode("utf-8"))
        assert body == {"error": "not found"}
    finally:
        conn.close()


def test_payload_invalid_json(running_server):
    """Submitting invalid/malformed JSON returns 400 Bad Request."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        conn.request("POST", "/api/process", body="{invalid_json}", headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        body = json.loads(res.read().decode("utf-8"))
        assert "invalid json" in body["error"].lower()
    finally:
        conn.close()


def test_payload_not_json_object(running_server):
    """Submitting a JSON list/array or string instead of object returns 400 Bad Request."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        conn.request("POST", "/api/process", body="[]", headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        body = json.loads(res.read().decode("utf-8"))
        assert "json object is required" in body["error"].lower()
    finally:
        conn.close()


def test_payload_missing_questionnaire(running_server):
    """Submitting empty object or missing questionnaire returns 400 Bad Request."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        # Missing key
        conn.request("POST", "/api/process", body="{}", headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        body = json.loads(res.read().decode("utf-8"))
        assert "questionnaire is required" in body["error"].lower()
        
        # Whitespace questionnaire
        payload = json.dumps({"questionnaire": "   \n  "})
        conn.request("POST", "/api/process", body=payload, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        body = json.loads(res.read().decode("utf-8"))
        assert "questionnaire is required" in body["error"].lower()
    finally:
        conn.close()


def test_missing_static_file_handling(running_server):
    """If a registered static file is missing on disk, return 404 gracefully instead of 500."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    
    # Patch PUBLIC_FILES to point to a non-existent path
    fake_public_files = PUBLIC_FILES.copy()
    fake_public_files["/static/app.js"] = Path("non_existent_file_path_12345.js")
    
    with patch("app.PUBLIC_FILES", fake_public_files):
        try:
            conn.request("GET", "/static/app.js")
            res = conn.getresponse()
            assert res.status == 404
            body = json.loads(res.read().decode("utf-8"))
            assert "not found" in body["error"].lower()
        finally:
            conn.close()


def test_negative_content_length_graceful_handling(running_server):
    """Sending a negative Content-Length header should not crash the server thread."""
    host, port = running_server.split(":")
    # We want to manually construct the request to control the Content-Length header
    # and not let the http client automatically set it.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        request = (
            "POST /api/process HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: -5\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("utf-8"))
        
        # Set a timeout so we don't hang if the server blocks waiting for input.
        # If the server blocks, we'll get a socket timeout, which we will catch and fail.
        sock.settimeout(2.0)
        try:
            response_data = sock.recv(4096).decode("utf-8")
            # If the server returned a response (even a 400/500/etc.) it didn't hang.
            assert "HTTP/" in response_data
        except socket.timeout:
            # If it timed out, it means the server thread hung reading from rfile!
            pytest.fail("Server hung or blocked indefinitely on negative Content-Length")
    finally:
        sock.close()


def test_extreme_content_length_rejection(running_server):
    """Sending a large Content-Length (e.g. 1 GB) without sending the bytes should not hang the thread forever."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Send header claiming 1 GB of data but don't send any body bytes
        request = (
            "POST /api/process HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 1000000000\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("utf-8"))
        
        # Set a timeout. If the server does not immediately reject or time out, it hangs.
        sock.settimeout(2.0)
        try:
            response_data = sock.recv(4096).decode("utf-8")
            # If it responded or closed connection, that's fine.
            assert "HTTP/" in response_data or not response_data
        except socket.timeout:
            # Server hung trying to read 1 GB of data!
            pytest.fail("Server hung waiting for large Content-Length payload body")
    finally:
        sock.close()


def test_keyboard_interrupt_graceful_shutdown():
    """Verify that starting app.py and sending KeyboardInterrupt stops the server and closes the socket."""
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
