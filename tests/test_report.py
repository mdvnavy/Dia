"""Formatted PDF intake report."""
from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("weasyprint")

from app import ClientDiscoveryHandler, build_intake_response
from client_discovery import report

FIXTURE = Path("tests/fixtures/complete_questionnaire.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result():
    return build_intake_response(FIXTURE)


def test_context_has_the_template_placeholders(result):
    context = report.build_context(result, report_date="October 23, 2026")
    for key in ("client", "date", "tier", "score", "profile_html", "analysis_html", "proposal_html", "missing"):
        assert key in context
    assert context["client"] == "Northstar Studio"
    assert context["score"] == "12"
    assert context["score_max"] == "20"
    assert context["tier"] == "B"
    assert context["tier_name"] == "Custom AI Agent"
    assert isinstance(context["missing"], list)
    assert "<h2>" not in context["profile_html"]  # doc headings are nested under the section heading
    assert "<h4>Overview</h4>" in context["profile_html"]


def test_missing_details_come_from_validation_issues():
    result = build_intake_response("| Question | Answer |\n| --- | --- |\n| Company name | Gappy Co |\n")
    context = report.build_context(result)
    assert any("Budget" in item for item in context["missing"])


def test_html_contains_sections_and_brand(result):
    html = report.render_html(result, report_date="October 23, 2026")
    for text in ("Northstar Studio", "October 23, 2026", ">B<", "/20", "Profile", "Analysis", "Proposal"):
        assert text in html
    css = (report.TEMPLATE_DIR / "intake-report.css").read_text(encoding="utf-8")
    assert "Made by BASH" in css
    assert "counter(page)" in css


def test_pdf_is_a_real_multi_page_document_with_embedded_fonts(result):
    pdf = report.render_pdf(result, report_date="October 23, 2026")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 10_000
    if shutil.which("pdfinfo"):  # object streams may be compressed, so ask poppler
        info = subprocess.run(["pdfinfo", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
        assert int(re.search(r"Pages:\s+(\d+)", info).group(1)) >= 1
        fonts = subprocess.run(["pdffonts", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
        assert "Inter" in fonts and "JetBrains" in fonts


def test_user_html_is_escaped_and_remote_resources_are_blocked():
    hostile = FIXTURE.replace(
        "Wants a practical demo before committing.",
        '<img src="http://169.254.169.254/latest/meta-data"> <script>alert(1)</script> ![x](http://example.com/x.png)',
    )
    result = build_intake_response(hostile)
    html = report.render_html(result)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert not report._is_allowed("http://169.254.169.254/latest/meta-data")
    assert not report._is_allowed("file:///etc/passwd")
    assert report._is_allowed((report.FONT_DIR / "Inter-Regular.ttf").resolve().as_uri())
    assert report.render_pdf(result).startswith(b"%PDF-")  # blocked image does not break rendering


def test_pdf_filename_is_slugged(result):
    assert report.pdf_filename(result) == "dia-northstar-studio.pdf"
    assert report.pdf_filename({"intake": {"company_name": "  "}}) == "dia-intake.pdf"


def _post(body: dict):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClientDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        connection.request("POST", "/api/pdf", body=json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, data
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pdf_endpoint_from_questionnaire():
    status, headers, data = _post({"questionnaire": FIXTURE, "date": "October 23, 2026"})
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert 'filename="dia-northstar-studio.pdf"' in headers["Content-Disposition"]
    assert data.startswith(b"%PDF-")


def test_pdf_endpoint_from_saved_result(result):
    status, _, data = _post({"result": result})
    assert status == 200 and data.startswith(b"%PDF-")


def test_pdf_endpoint_requires_input():
    status, _, data = _post({})
    assert status == 400
    assert json.loads(data)["error"] == "questionnaire or result is required"


def test_pdf_document_properties(result):
    """Glyph nit (a): Author, Creator and Title are set on the PDF itself."""
    pdf = report.render_pdf(result, report_date="October 23, 2026")
    if not shutil.which("pdfinfo"):
        pytest.skip("pdfinfo (poppler) not installed")
    info = subprocess.run(["pdfinfo", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
    client = result["intake"]["company_name"]
    assert re.search(rf"^Title:\s+{re.escape(client)} — Discovery Intake$", info, re.M)
    assert re.search(r"^Author:\s+Bash Systems$", info, re.M)
    assert re.search(r"^Creator:\s+Discovery Intake Agent$", info, re.M)
