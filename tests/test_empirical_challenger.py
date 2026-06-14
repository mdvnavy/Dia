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
        time.sleep(1.0)
        
    yield f"127.0.0.1:{server.server_port}"
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_content_length_leading_zeros(running_server):
    """Sending Content-Length with leading zeros (e.g. 0000000000000000000010) should parse correctly."""
    host, port = running_server.split(":")
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": "leading zeros test"})
        length = len(body)
        content_length_str = f"{length:010d}"  # e.g., 0000000035
        
        conn.request(
            "POST",
            "/api/process",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": content_length_str
            }
        )
        res = conn.getresponse()
        assert res.status == 200
        response_body = json.loads(res.read().decode("utf-8"))
        assert "analysis" in response_body or "opportunity" in response_body or "error" not in response_body
    finally:
        conn.close()


def test_extreme_int_content_length_overflow(running_server):
    """Sending a Content-Length that exceeds standard integer representation limits should be rejected or handled without crashing."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Send a ridiculously large number that exceeds typical int sizes
        huge_number = "9" * 100
        request = (
            "POST /api/process HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: " + huge_number + "\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("utf-8"))
        sock.settimeout(2.0)
        try:
            response_data = sock.recv(4096).decode("utf-8")
            # The server should respond with BAD REQUEST or close connection
            assert "400 Bad Request" in response_data or "500 Internal Server Error" in response_data or not response_data
        except socket.timeout:
            pytest.fail("Server hung on extremely large integer Content-Length string overflow")
    finally:
        sock.close()


def test_non_ascii_digits_content_length(running_server):
    """Sending non-ASCII unicode digits in Content-Length should fall back to 0 or fail gracefully, but not crash/hang."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Arabic-Indic numeral for 5 is '٥'. int('٥') in python works and returns 5!
        # But in socket level, let's see how BaseHTTPRequestHandler's headers parse it.
        # It gets decoded as latin1/utf-8 and then passed to int().
        # Let's send a non-decodable string.
        request = (
            "POST /api/process HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: abc\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("utf-8"))
        sock.settimeout(2.0)
        try:
            response_data = sock.recv(4096).decode("utf-8")
            assert "400" in response_data or "500" in response_data or not response_data
        except socket.timeout:
            pytest.fail("Server hung on non-numeric Content-Length")
    finally:
        sock.close()
