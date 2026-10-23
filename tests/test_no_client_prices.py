"""Ikenna: 'Yes, remove it to match the site' (the site says "Contact for pricing").

Every client-facing output (Markdown, JSON, HTML, PDF text, API responses, the chat agent's scoring tool) is
rendered for samples covering all three tiers and checked: no tier price band value appears (the old bands and
the new owner bands), and no dollar range $X-$Y appears except inside the client's own answers.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.request
from dataclasses import fields
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from client_discovery.core import parse_questionnaire_markdown, score_opportunity
from client_discovery.models import ClientIntake

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / "samples/sample_questionnaire.md").read_text(encoding="utf-8")

OLD_BANDS = ("$500", "$2,500", "$10,000", "$25,000")
NEW_OWNER_BANDS = ("$2,500", "$7,500", "$5,500", "$12,500", "$10,000", "$22,000")  # Proto: owner workspace values
BAND_VALUES = tuple(dict.fromkeys(OLD_BANDS + NEW_OWNER_BANDS))
# Any band value also caught without the comma or as a bare number next to a dollar sign or "k".
BAND_PATTERNS = [re.compile(rf"\$\s?{re.escape(v[1:])}(?![\d,])") for v in BAND_VALUES] + [
    re.compile(rf"\$\s?{re.escape(v[1:].replace(',', ''))}(?![\d,])") for v in BAND_VALUES] + [
    re.compile(rf"\$\s?{n}(?:\.0)?\s?[kK]\b") for n in ("2.5", "7.5", "5.5", "12.5", "10", "22", "25")]
DOLLAR_RANGE = re.compile(r"\$\s?\d[\d,.]*\s?[kKmM]?\s*(?:-|–|—|to)\s*\$?\s?\d[\d,.]*\s?[kKmM]?")


def _replace(text: str, question_start: str, answer: str) -> str:
    out, n = re.subn(rf"^(\| {re.escape(question_start)}[^|]*\|)[^\n]*\|$", lambda m: f"{m.group(1)} {answer} |",
                     text, flags=re.M)
    assert n == 1, question_start
    return out


def _drop_row(text: str, question_start: str) -> str:
    out, n = re.subn(rf"^\| {re.escape(question_start)}[^\n]*\n", "", text, flags=re.M)
    assert n == 1, question_start
    return out


QUICK_WIN = _replace(_drop_row(_replace(SAMPLE, "What tools/software", ""), "What breaks most often"),
                     "Budget range", "About $3k")
FULL_INTEGRATION = _replace(SAMPLE.replace(
    "| What breaks most often? |",
    "| What else slows the team? | Quotes are rebuilt by hand. |\n"
    "| What else is manual? | Invoices are re-keyed into accounting. |\n"
    "| What breaks most often? |", 1), "Budget range", "$15,000 to $20,000")
SAMPLES = {
    "quick-win": QUICK_WIN,
    "custom-agent-band-budget": SAMPLE,  # the client's own budget equals the old band: only it may show it
    "custom-agent": _replace(SAMPLE, "Budget range", "$6,000-$9,000"),
    "full-integration": FULL_INTEGRATION,
    "no-budget": _replace(SAMPLE, "Budget range", ""),
}
EXPECTED_TIERS = {"quick-win": "Quick Win", "custom-agent-band-budget": "Custom AI Agent",
                  "custom-agent": "Custom AI Agent", "full-integration": "Full Integration",
                  "no-budget": "Custom AI Agent"}


def client_answers(text: str) -> list[str]:
    intake = parse_questionnaire_markdown(text)
    values = []
    for f in fields(ClientIntake):
        value = getattr(intake, f.name)
        values += [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return sorted({v.strip() for v in values if isinstance(v, str) and v.strip()}, key=len, reverse=True)


def assert_no_prices(output: str, text: str, where: str) -> None:
    for answer in client_answers(text):  # the client's own words may be echoed back, prices included
        output = output.replace(answer, " ").replace(json.dumps(answer)[1:-1], " ")
    for pattern in BAND_PATTERNS:
        match = pattern.search(output)
        assert not match, f"{where}: tier band value {match.group(0)!r} in client output"
    match = DOLLAR_RANGE.search(output)
    assert not match, f"{where}: dollar range {match.group(0)!r} in client output"
    assert "price_range" not in output and "Estimated range" not in output and "Price range" not in output, where


def test_samples_cover_all_three_tiers():
    tiers = {name: score_opportunity(parse_questionnaire_markdown(text)).tier for name, text in SAMPLES.items()}
    assert tiers == EXPECTED_TIERS
    assert set(tiers.values()) == {"Quick Win", "Custom AI Agent", "Full Integration"}


def test_the_band_still_exists_internally_for_the_owner_cross_check():
    assert score_opportunity(parse_questionnaire_markdown(SAMPLE)).price_range == "$2,500-$10,000"


def test_the_checker_catches_a_band_and_a_range():
    with pytest.raises(AssertionError):
        assert_no_prices("fits at $2,500-$10,000", SAMPLES["custom-agent"], "self-check")
    with pytest.raises(AssertionError):
        assert_no_prices("from $5.5k", SAMPLES["custom-agent"], "self-check")
    assert_no_prices("A stated budget of $6,000-$9,000 fits this tier", SAMPLES["custom-agent"], "self-check")


@pytest.mark.parametrize("name", SAMPLES)
def test_markdown_and_json_exports_show_no_tier_price(name):
    from app import build_intake_response
    from client_discovery.core import scrub_for_export
    from client_discovery.report_model import MD_EXPORT_ORDER

    text = SAMPLES[name]
    result = build_intake_response(text)
    assert "price_range" not in result["score"]
    safe = scrub_for_export(result)
    md = "\n\n---\n\n".join(safe["documents"][doc] for doc in MD_EXPORT_ORDER)
    assert "Contact for pricing" in md
    assert_no_prices(md, text, f"{name} Markdown")
    assert_no_prices(json.dumps(result, ensure_ascii=False), text, f"{name} JSON")


@pytest.mark.parametrize("name", SAMPLES)
def test_html_and_pdf_show_no_tier_price(name):
    pytest.importorskip("weasyprint")
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    from app import build_intake_response
    from client_discovery.report import render_html, render_pdf

    text = SAMPLES[name]
    result = build_intake_response(text)
    html = render_html(result, report_date="October 23, 2026")
    assert_no_prices(html, text, f"{name} HTML")
    pdf = subprocess.run(["pdftotext", "-", "-"], input=render_pdf(result, report_date="October 23, 2026"),
                         capture_output=True, check=True).stdout.decode()
    assert "Contact for pricing" in pdf
    assert_no_prices(" ".join(pdf.split()), text, f"{name} PDF")


def test_service_path_and_chat_agent_tool_show_no_tier_price():
    from unittest.mock import patch
    from types import SimpleNamespace

    from character import score_client_opportunity
    from client_discovery import service

    for name, text in SAMPLES.items():
        tool = score_client_opportunity(text)
        assert "price_range" not in tool
        assert_no_prices(json.dumps(tool, ensure_ascii=False), text, f"{name} agent tool")
        with patch.object(service, "root_agent", SimpleNamespace(run=lambda prompt: SimpleNamespace(text=""))), \
                patch.object(service, "trigger_obs_screenshot", lambda *a, **k: ""), \
                patch.object(service, "save_obs_replay_buffer", lambda *a, **k: ""):
            result = service.build_intake_response(text)
        result.pop("strategic_analysis", None)
        assert_no_prices(json.dumps(result, ensure_ascii=False), text, f"{name} service JSON")


def _server():
    from app import ClientDiscoveryHandler

    for port in range(8096, 8101):  # Forge rule: 8095-8100 only (8095 is left for previews)
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), ClientDiscoveryHandler)
        except OSError:
            continue
    pytest.skip("no free port in 8096-8100")


def test_api_responses_show_no_tier_price(monkeypatch):
    monkeypatch.delenv("MEMORY_SHEET_ID", raising=False)
    monkeypatch.delenv("OBS_CAPTURE", raising=False)
    server = _server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    try:
        for name, text in SAMPLES.items():
            raw = post("/api/process", {"questionnaire": text})
            result = json.loads(raw)
            assert "price_range" not in result["score"]
            assert_no_prices(raw.decode(), text, f"{name} /api/process")
            if shutil.which("pdftotext"):
                pdf = post("/api/pdf", {"questionnaire": text, "result": result, "date": "October 23, 2026"})
                pdf_text = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True,
                                          check=True).stdout.decode()
                assert_no_prices(" ".join(pdf_text.split()), text, f"{name} /api/pdf")
    finally:
        server.shutdown()
        server.server_close()
