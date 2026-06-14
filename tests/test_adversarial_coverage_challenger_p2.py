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
from client_discovery.service import build_intake_response
from client_discovery.core import (
    parse_questionnaire_markdown,
    validate_intake,
    score_opportunity,
    generate_documents,
)
from client_discovery.models import ClientIntake, OpportunityScore


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


# --- Group 1: Type Mismatch Verification ---

def test_type_mismatches_direct_core_apis():
    """Verify that core Python APIs handle non-standard types gracefully without crashing if possible, or raise expected errors."""
    
    # Test parse_questionnaire_markdown with non-string input (should raise AttributeError or TypeError)
    with pytest.raises((AttributeError, TypeError)):
        parse_questionnaire_markdown(12345)  # type: ignore
        
    with pytest.raises((AttributeError, TypeError)):
        parse_questionnaire_markdown(None)  # type: ignore

    # Test validate_intake with None
    with pytest.raises((AttributeError, TypeError)):
        validate_intake(None)  # type: ignore

    # Test score_opportunity with None
    with pytest.raises((AttributeError, TypeError)):
        score_opportunity(None)  # type: ignore


def test_type_mismatches_api_payload(running_server):
    """Verify how the HTTP API handles various type mismatches in the JSON payload."""
    host, port = running_server.split(":")
    
    # 1. questionnaire as an integer
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": 12345})
        conn.request("POST", "/api/process", body=body, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        # Since app.py does str(payload.get("questionnaire", "")), 12345 gets cast to "12345".
        # This will be parsed as a string, having validation errors but not crashing (status 200).
        assert res.status == 200
        res.read()
    finally:
        conn.close()

    # 2. questionnaire as null (None)
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": None})
        conn.request("POST", "/api/process", body=body, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        # null gets cast to "None" which is not empty, status 200.
        assert res.status == 200
        res.read()
    finally:
        conn.close()

    # 3. questionnaire as a boolean
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": True})
        conn.request("POST", "/api/process", body=body, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        # True gets cast to "True", status 200.
        assert res.status == 200
        res.read()
    finally:
        conn.close()


# --- Group 2: Empty/Whitespace Inputs Verification ---

def test_whitespace_and_empty_payloads(running_server):
    """Verify that empty, whitespace, or missing inputs return 400 Bad Request."""
    host, port = running_server.split(":")
    
    # Empty string
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": ""})
        conn.request("POST", "/api/process", body=body, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        response_body = json.loads(res.read().decode("utf-8"))
        assert "questionnaire is required" in response_body["error"]
    finally:
        conn.close()

    # Whitespace only
    conn = http.client.HTTPConnection(host, int(port))
    try:
        body = json.dumps({"questionnaire": "   \n  \t   "})
        conn.request("POST", "/api/process", body=body, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        assert res.status == 400
        response_body = json.loads(res.read().decode("utf-8"))
        assert "questionnaire is required" in response_body["error"]
    finally:
        conn.close()


# --- Group 3: Server Thread and Transport Fault Injection ---

def test_client_abrupt_disconnect_mid_request(running_server):
    """Verify that if a client connects and abruptly disconnects mid-request, the server thread handles it without crashing."""
    host, port = running_server.split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, int(port)))
        # Send partial request
        sock.sendall(b"POST /api/process HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100\r\n\r\n")
        # Abruptly close socket with RST
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b'\x01\x00\x00\x00\x00\x00\x00\x00')
    finally:
        sock.close()
    
    # Verify server is still alive by making a healthy request
    time.sleep(0.1)
    conn = http.client.HTTPConnection(host, int(port))
    try:
        conn.request("GET", "/api/sample")
        res = conn.getresponse()
        assert res.status == 200
        res.read()
    finally:
        conn.close()


def test_concurrent_connections_stress(running_server):
    """Verify the CustomThreadingHTTPServer's ability to handle concurrent connections under stress."""
    host, port = running_server.split(":")
    
    num_threads = 10
    errors = []
    
    def worker():
        try:
            conn = http.client.HTTPConnection(host, int(port), timeout=2.0)
            conn.request("GET", "/api/sample")
            res = conn.getresponse()
            if res.status != 200:
                errors.append(f"Expected 200, got {res.status}")
            res.read()
            conn.close()
        except Exception as e:
            errors.append(str(e))
            
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    assert not errors, f"Concurrent requests had errors: {errors}"
