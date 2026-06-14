import http.client
import json
import threading
from http.server import ThreadingHTTPServer
import pytest

from app import ClientDiscoveryHandler
from client_discovery.export import markdown_to_txt, markdown_to_docx, markdown_to_pdf

def _request(method: str, path: str, body: str | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            method,
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        headers = response.getheaders()
        raw = response.read()
        status = response.status
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return status, headers, raw

def test_markdown_to_txt():
    md = "# Title\n## Header 2\nSome text.\n---\nMore text."
    txt = markdown_to_txt(md)
    assert "TITLE" in txt
    assert "Header 2" in txt
    assert "Some text." in txt
    assert "More text." in txt
    assert "\n---\n" not in txt
    assert "-" * 40 in txt

def test_markdown_to_docx():
    md = "# Title\n- Bullet 1\n- Bullet 2\n\n| H1 | H2 |\n|---|---|\n| C1 | C2 |"
    docx_bytes = markdown_to_docx(md)
    # DOCX is a ZIP file, so it starts with PK signature
    assert docx_bytes.startswith(b"PK")

def test_markdown_to_pdf():
    md = "# Title\nParagraph text here.\n- Bullet\n\n| H1 | H2 |\n|---|---|\n| C1 | C2 |"
    pdf_bytes = markdown_to_pdf(md)
    # PDF starts with %PDF signature
    assert pdf_bytes.startswith(b"%PDF")

def test_api_export_endpoint_success():
    md = "# Title\nThis is a test document."
    
    # Test TXT
    status, headers, body = _request("POST", "/api/export", json.dumps({"format": "txt", "markdown": md}))
    assert status == 200
    assert any(h[0].lower() == "content-type" and "text/plain" in h[1].lower() for h in headers)
    assert b"TITLE" in body

    # Test DOCX
    status, headers, body = _request("POST", "/api/export", json.dumps({"format": "docx", "markdown": md}))
    assert status == 200
    assert any(h[0].lower() == "content-type" and "vnd.openxmlformats" in h[1].lower() for h in headers)
    assert body.startswith(b"PK")

    # Test PDF
    status, headers, body = _request("POST", "/api/export", json.dumps({"format": "pdf", "markdown": md}))
    assert status == 200
    assert any(h[0].lower() == "content-type" and "pdf" in h[1].lower() for h in headers)
    assert body.startswith(b"%PDF")

def test_api_export_endpoint_errors():
    # Test missing markdown
    status, headers, body = _request("POST", "/api/export", json.dumps({"format": "pdf"}))
    assert status == 400
    err = json.loads(body.decode("utf-8"))
    assert "required" in err["error"]

    # Test invalid format
    status, headers, body = _request("POST", "/api/export", json.dumps({"format": "xyz", "markdown": "test"}))
    assert status == 400
    err = json.loads(body.decode("utf-8"))
    assert "unsupported" in err["error"]
