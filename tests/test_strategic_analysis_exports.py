"""Persona v1.2 rule 1 (no AI text in client exports) for strategic_analysis: the Gemini draft (optionally
Jules-refined) that client_discovery/service.py builds, and its "DIA Agent Error" fallback. Neither may reach
an export (md, JSON, zip, PDF), whichever path produced it."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import build_intake_response as app_build
from client_discovery import core
from tests.test_persona_v12 import COMPLETE, MISSING, base_url, browser, context, open_page, run_intake  # noqa: F401

FAKE = "Strategic read: FAKE-GEMINI-ANALYSIS-7Q. DIA Agent Error: 503 upstream unavailable"
FAKE_MARKERS = ("FAKE-GEMINI-ANALYSIS-7Q", "dia agent error", "Strategic read")


def assert_clean(text: str, where: str) -> None:
    for marker in FAKE_MARKERS:
        assert marker.lower() not in text.lower(), (where, marker)


def tainted(result: dict) -> dict:
    """A result as an old or unwired path could have produced it: draft beside and inside the documents."""
    bad = json.loads(json.dumps(result))
    bad["strategic_analysis"] = FAKE
    docs = bad["documents"]
    docs["opportunity-analysis.md"] += f"\n\n{FAKE}\n\n## More\nFAKE-GEMINI-ANALYSIS-7Q again"
    docs["client-profile.md"] += "\nDIA Agent Error: GEMINI_API_KEY is missing or invalid."
    docs["proposal-draft.md"] = "DIA Agent Error: timeout\n" + docs["proposal-draft.md"]
    return bad


def pdf_text(body: bytes) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    return subprocess.run(["pdftotext", "-", "-"], input=body, capture_output=True, check=True).stdout.decode()


# --- Where strategic_analysis can come from ----------------------------------------------------

def test_generate_documents_takes_no_ai_text():
    intake = core.parse_questionnaire_markdown(COMPLETE)
    with pytest.raises(TypeError):
        core.generate_documents(intake, core.score_opportunity(intake), FAKE)  # pylint: disable=too-many-function-args


def test_web_app_path_never_produces_strategic_analysis():
    result = app_build(COMPLETE)
    assert "strategic_analysis" not in result


@pytest.fixture
def no_obs():
    with patch("client_discovery.service.trigger_obs_screenshot"), patch("client_discovery.service.save_obs_replay_buffer"):
        yield


@pytest.mark.parametrize("env", [{"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "k", "JULES_API_KEY": ""}], ids=["missing-key", "draft"])
def test_service_keeps_the_draft_beside_the_documents(env, no_obs, monkeypatch):
    from client_discovery import service

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    agent = MagicMock()
    agent.run.return_value = SimpleNamespace(text=FAKE)
    monkeypatch.setattr(service, "root_agent", agent)
    result = service.build_intake_response(MISSING)
    assert "dia agent error" in result["strategic_analysis"].lower()  # screen-only field
    for name, doc in result["documents"].items():
        assert_clean(doc, name)
    assert result["documents"] == app_build(MISSING)["documents"]  # same deterministic docs as the app


def test_service_gemini_exception_stays_out_of_documents(no_obs, monkeypatch):
    from client_discovery import service

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    agent = MagicMock()
    agent.run.side_effect = RuntimeError("FAKE-GEMINI-ANALYSIS-7Q quota exceeded")
    monkeypatch.setattr(service, "root_agent", agent)
    result = service.build_intake_response(COMPLETE)
    assert result["strategic_analysis"].startswith("DIA Agent Error:")
    assert_clean(json.dumps(result["documents"]), "documents")


# --- The export guard -------------------------------------------------------------------------

@pytest.mark.parametrize("text", [COMPLETE, MISSING], ids=["complete", "missing"])
def test_scrub_is_a_no_op_on_deterministic_results(text):
    result = app_build(text)
    assert core.scrub_for_export(result) == result


def test_scrub_removes_strategic_analysis_and_error_lines():
    safe = core.scrub_for_export(tainted(app_build(MISSING)))
    assert "strategic_analysis" not in safe
    assert_clean(json.dumps(safe), "scrubbed result")
    assert safe["documents"] == app_build(MISSING)["documents"]


def test_pdf_text_from_a_tainted_result_is_clean():
    pytest.importorskip("weasyprint")
    from client_discovery.report import render_pdf

    text = pdf_text(render_pdf(tainted(app_build(MISSING)), report_date="October 23, 2026"))
    assert "Why this tier" in text
    assert_clean(text, "pdf")


def test_api_pdf_result_only_body_is_clean(base_url):  # noqa: F811
    pytest.importorskip("weasyprint")
    import urllib.request

    body = json.dumps({"result": tainted(app_build(COMPLETE)), "date": "October 23, 2026"}).encode()
    req = urllib.request.Request(base_url + "/api/pdf", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        assert resp.status == 200
        assert_clean(pdf_text(resp.read()), "/api/pdf")


# --- Browser exports: md, JSON, zip -----------------------------------------------------------

TAINT_JS = """fake => {
  const taint = (r) => { r.strategic_analysis = fake;
    r.documents['opportunity-analysis.md'] += `\\n\\n${fake}\\n\\n## More\\nFAKE-GEMINI-ANALYSIS-7Q again`;
    r.documents['client-profile.md'] += '\\nDIA Agent Error: GEMINI_API_KEY is missing or invalid.';
    r.documents['proposal-draft.md'] = 'DIA Agent Error: timeout\\n' + r.documents['proposal-draft.md']; return r; };
  taint(latestPayload); documents = latestPayload.documents;
  const items = readCabinet(); items.forEach((item) => taint(item.result)); writeCabinet(items);
  return items[0].id;
}"""


def test_browser_md_json_and_zip_exports_are_clean(context, base_url):  # noqa: F811
    page = open_page(context, base_url)
    run_intake(page, MISSING)
    clean_md = page.evaluate("buildMarkdownExport()")
    entry_id = page.evaluate(TAINT_JS, FAKE)
    assert "FAKE-GEMINI-ANALYSIS-7Q" in page.evaluate("JSON.stringify(latestPayload)")  # the taint took

    assert page.evaluate("buildMarkdownExport()") == clean_md
    for button in ("#downloadMarkdown", "#downloadJson"):
        with page.expect_download() as download:
            page.evaluate("b => document.querySelector(b).click()", button)
        assert_clean(open(download.value.path(), encoding="utf-8").read(), button)

    pdf_bodies = []
    page.on("request", lambda r: pdf_bodies.append(r.post_data) if r.url.endswith("/api/pdf") else None)
    page.evaluate("id => { CabinetDrawer.open(); CabinetDrawer.setSelectMode(true, { preselect: [id] }); }", entry_id)
    with page.expect_download(timeout=60000) as download:
        page.evaluate("CabinetDrawer.exportSelected()")
    archive = zipfile.ZipFile(io.BytesIO(open(download.value.path(), "rb").read()))
    names = archive.namelist()
    assert any(n.endswith(".md") for n in names) and any(n.endswith(".json") for n in names)
    assert any(n.endswith(".pdf") for n in names)
    for name in names:
        data = archive.read(name)
        assert_clean(pdf_text(data) if name.endswith(".pdf") else data.decode(), name)
    assert pdf_bodies and all("strategic_analysis" not in b and "FAKE-GEMINI" not in b for b in pdf_bodies)
    assert page.errors == []
