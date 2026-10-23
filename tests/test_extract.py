"""Upload tray: file -> questionnaire text extraction."""
from __future__ import annotations

import http.client
import io
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app import ClientDiscoveryHandler, build_intake_response
from client_discovery import extract
from client_discovery.extract import MAX_UPLOAD_BYTES, ExtractionError, extract_text
from client_discovery.core import parse_questionnaire_markdown

FIXTURE = Path("tests/fixtures/complete_questionnaire.md").read_text(encoding="utf-8")


def test_markdown_and_text_pass_through():
    assert extract_text("intake.md", FIXTURE.encode()) == FIXTURE.strip() + "\n"
    assert "Northstar Studio" in extract_text("intake.txt", FIXTURE.encode("utf-8-sig"))


def test_cp1252_text_is_decoded_not_rejected():
    assert "Café" in extract_text("notes.txt", "Café notes".encode("cp1252"))


def test_csv_becomes_a_parseable_question_answer_table():
    csv_bytes = b"Question,Answer\nCompany name,Acme Ltd\nBudget range,\"$5,000-$10,000\"\nFinal decision-maker,CEO\n"
    text = extract_text("intake.csv", csv_bytes)
    assert "| Company name | Acme Ltd |" in text
    intake = parse_questionnaire_markdown(text)
    assert intake.company_name == "Acme Ltd"
    assert intake.budget == "$5,000-$10,000"
    assert intake.decision_maker == "CEO"


def test_tsv_and_pipes_are_escaped():
    text = extract_text("intake.tsv", b"Question\tAnswer\nTools\tSlack | Notion\n")
    assert "Slack \\| Notion" in text


def test_json_questionnaire_field_and_flat_objects():
    assert extract_text("a.json", json.dumps({"questionnaire": FIXTURE}).encode()).startswith("# ")
    text = extract_text("b.json", json.dumps({"Company name": "Acme", "Tools": ["Slack", "Notion"]}).encode())
    assert "| Company name | Acme |" in text
    assert "| Tools | Slack; Notion |" in text
    assert parse_questionnaire_markdown(text).company_name == "Acme"


def test_invalid_json_gives_friendly_error():
    with pytest.raises(ExtractionError, match="isn't valid JSON"):
        extract_text("bad.json", b"{nope")


def test_rtf_is_stripped_to_text():
    rtf = rb"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}\f0\pard Company name: Acme\par Budget: $5k\par}"
    text = extract_text("intake.rtf", rtf)
    assert "Company name: Acme" in text
    assert "\\par" not in text


def test_docx_headings_paragraphs_and_tables_survive_round_trip():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Discovery Intake Questionnaire", level=0)
    document.add_heading("1. Company Basics", level=1)
    table = document.add_table(rows=3, cols=2)
    for row, (question, answer) in zip(table.rows, [("Question", "Answer"), ("Company name", "Docx Co"), ("Location", "Lagos")]):
        row.cells[0].text, row.cells[1].text = question, answer
    document.add_paragraph("Wants a demo first.")
    buffer = io.BytesIO()
    document.save(buffer)

    text = extract_text("intake.docx", buffer.getvalue())
    assert text.startswith("# Discovery Intake Questionnaire")
    assert "## 1. Company Basics" in text
    assert "| Company name | Docx Co |" in text
    assert "Wants a demo first." in text
    assert build_intake_response(text)["intake"]["company_name"] == "Docx Co"


def test_corrupt_docx_is_a_friendly_error():
    with pytest.raises(ExtractionError, match="couldn't be opened"):
        extract_text("broken.docx", b"not a zip")


def test_doc_without_a_converter_explains_how_to_fix(monkeypatch):
    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    with pytest.raises(ExtractionError, match="save it as .docx"):
        extract_text("old.doc", b"\xd0\xcf\x11\xe0 legacy word bytes")


@pytest.mark.parametrize("name", ["photo.png", "deck.pdf", "noextension"])
def test_unsupported_types_are_rejected(name):
    with pytest.raises(ExtractionError, match="can't read"):
        extract_text(name, b"data")


def test_size_cap_and_empty_files():
    with pytest.raises(ExtractionError) as error:
        extract_text("big.txt", b"a" * (MAX_UPLOAD_BYTES + 1))
    assert error.value.status == 413
    with pytest.raises(ExtractionError, match="empty"):
        extract_text("empty.txt", b"")
    with pytest.raises(ExtractionError, match="No readable text"):
        extract_text("blank.md", b"   \n\n")


def _post_raw(path: str, body: bytes, filename: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request("POST", path, body=body, headers={"Content-Type": "application/octet-stream", "X-Filename": filename})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_extract_endpoint_returns_questionnaire_text():
    status, payload = _post_raw("/api/extract", FIXTURE.encode(), "my%20intake.md")
    assert status == 200
    assert payload["filename"] == "my intake.md"
    assert "Northstar Studio" in payload["questionnaire"]


def test_extract_endpoint_rejects_unsupported_type():
    status, payload = _post_raw("/api/extract", b"x", "a.exe")
    assert status == 400
    assert "can't read" in payload["error"]


def test_extract_endpoint_rejects_oversized_body_before_reading():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.putrequest("POST", "/api/extract")
        connection.putheader("Content-Length", str(MAX_UPLOAD_BYTES + 1))
        connection.putheader("X-Filename", "big.txt")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        assert "5 MB" in json.loads(response.read())["error"]
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
