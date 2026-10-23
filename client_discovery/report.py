"""Formatted PDF intake report (WeasyPrint + a Jinja template).

The template lives in templates/report/ (intake-report.html + intake-report.css).
dia-savant v1.0: the report is answer-first (§3.1). build_context rebuilds the intake, score, issues
and report model (client_discovery.report_model) from the intake answers alone, and passes:
client, date, tier (letter), tier_name, score, score_max, cover_sentence, sections (no, label,
title, html), exhibit (Exhibit 1 data), method_and_sources (§5, verbatim) and missing. The
per-document profile_html, analysis_html and proposal_html are still passed for other templates.

User-supplied text is HTML-escaped before Markdown rendering, and the URL
fetcher only serves files from the report template and font directories, so a
questionnaire can never make the server fetch a remote or local URL.
"""
from __future__ import annotations

import html
import re
from datetime import date as _date
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates" / "report"
FONT_DIR = TEMPLATE_DIR / "fonts"
TEMPLATE_NAME = "intake-report.html"
ALLOWED_ROOTS = (TEMPLATE_DIR.resolve(),)

# The report shows the tier as a single letter. DIA's tiers are named, so map
# them here in ascending scope (one place to change if the scheme changes).
# Letterhead variant in Glyph's template: "tucked" (Ikenna's pick), "wide" or "" (plain).
REPORT_CORNERS = "tucked"

TIER_LETTERS = {"Quick Win": "A", "Custom AI Agent": "B", "Full Integration": "C"}

DOC_KEYS = {
    "profile_html": "client-profile.md",
    "analysis_html": "opportunity-analysis.md",
    "proposal_html": "proposal-draft.md",
}


def markdown_to_html(text: str) -> str:
    import markdown

    body = (text or "").strip()
    # The section heading comes from the template, so drop the doc's own H1.
    body = re.sub(r"\A#\s+[^\n]*\n+", "", body)
    # Nest the doc's headings under the template's section heading.
    body = re.sub(r"(?m)^(#{1,5})(\s)", r"#\1\2", body)
    return markdown.markdown(html.escape(body, quote=False), extensions=["tables", "sane_lists"])


def _is_allowed(url: str) -> bool:
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme != "file":
        return False
    path = Path(unquote(parsed.path)).resolve()
    return path.is_file() and any(root in path.parents for root in ALLOWED_ROOTS)


def _safe_fetcher() -> Any:
    """A WeasyPrint URL fetcher that only reads bundled template/font files."""
    from weasyprint.urls import URLFetcher

    class LocalOnlyFetcher(URLFetcher):
        def fetch(self, url: str, headers: Any = None) -> Any:
            if not _is_allowed(url):
                raise ValueError(f"blocked resource: {url}")
            return super().fetch(url, headers)

    return LocalOnlyFetcher(allowed_protocols=("file",))


def intake_from_result(result: dict[str, Any]):
    """The ClientIntake held in a saved result, coerced field by field. Nothing else in the result is used."""
    from dataclasses import fields

    from client_discovery.models import ClientIntake

    raw = result.get("intake") if isinstance(result.get("intake"), dict) else {}
    kwargs = {}
    for f in fields(ClientIntake):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if f.name in ("pain_points", "goals"):
            value = [str(v) for v in value] if isinstance(value, (list, tuple)) else ([str(value)] if value else [])
        elif f.name == "workflows_section":
            value = bool(value)
        else:
            value = "" if value is None else str(value)
        kwargs[f.name] = value
    return ClientIntake(**kwargs)


def _rebuild(result: dict[str, Any], report_date: str | None) -> dict[str, Any]:
    """Rebuild the report deterministically from the intake answers. Client-supplied documents, scores and
    issues are ignored, so nothing but DIA's own templates can reach the PDF (persona v1.2 rule 1)."""
    from client_discovery.core import score_opportunity, validate_intake
    from client_discovery.report_model import build_report

    intake = intake_from_result(result)
    score = score_opportunity(intake)
    issues = validate_intake(intake)
    return {"intake": intake, "score": score, "issues": issues,
            "report": build_report(intake, score, issues, report_date)}


def build_context(result: dict[str, Any], *, report_date: str | None = None) -> dict[str, Any]:
    from dataclasses import asdict

    from client_discovery.core import client_score_dict, scrub_for_export
    from client_discovery.report_model import render_documents

    result = scrub_for_export(result)  # no AI text in client exports (persona v1.2 rule 1)
    rebuilt = _rebuild(result, report_date or _date.today().strftime("%B %-d, %Y"))
    intake, score, issues, model = rebuilt["intake"], rebuilt["score"], rebuilt["issues"], rebuilt["report"]
    sections = []
    for section in model["sections"]:
        item = dict(section)
        item["html"] = "" if section["key"] == "method" else markdown_to_html(section["body"])
        sections.append(item)
    context: dict[str, Any] = {
        "corners": REPORT_CORNERS,
        "client": intake.company_name.strip() or "Untitled client",
        "date": model["date"],
        "tier": TIER_LETTERS.get(score.tier, score.tier[:1].upper()),
        "tier_name": score.tier,
        "score": str(score.total_score),
        "score_max": str(score.max_score),
        "cover_sentence": model["cover_sentence"],
        "sections": sections,
        "exhibit": model["exhibit"],
        "method_and_sources": model["method_and_sources"],
        "missing": list(model["missing"]),
        "actions": list(model["actions"]),
        "next_steps": model.get("next_steps"),
        "score_detail": client_score_dict(score),
        "reasons": list(score.reasons),
        "timeline": score.timeline,
        "issues": [asdict(issue) for issue in issues],
        "intake": asdict(intake),
    }
    documents = render_documents(model)
    for key, doc_name in DOC_KEYS.items():
        context[key] = markdown_to_html(documents[doc_name])
    return context


def render_html(result: dict[str, Any], *, report_date: str | None = None) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
    template = env.get_template(TEMPLATE_NAME)
    context = build_context(result, report_date=report_date)
    # *_html values are already escaped-then-rendered Markdown.
    from markupsafe import Markup

    for key in DOC_KEYS:
        context[key] = Markup(context[key])
    for section in context["sections"]:
        section["html"] = Markup(section["html"])
    context["font_dir"] = FONT_DIR.resolve().as_uri()
    return template.render(**context)


def render_pdf(result: dict[str, Any], *, report_date: str | None = None) -> bytes:
    from weasyprint import HTML

    document = HTML(
        string=render_html(result, report_date=report_date),
        base_url=TEMPLATE_DIR.resolve().as_uri() + "/",
        url_fetcher=_safe_fetcher(),
    )
    rendered = document.render()
    # Glyph nit (a): document properties. WeasyPrint writes `generator` as the PDF Creator.
    client = str((result.get("intake") or {}).get("company_name") or "").strip() or "Client"
    rendered.metadata.title = f"{client} — Discovery Intake"
    rendered.metadata.authors = ["Bash Systems"]
    rendered.metadata.generator = "Discovery Intake Agent"
    return rendered.write_pdf()


def pdf_filename(result: dict[str, Any]) -> str:
    client = str((result.get("intake") or {}).get("company_name") or "intake")
    slug = re.sub(r"[^a-z0-9]+", "-", client.lower()).strip("-") or "intake"
    return f"dia-{slug}.pdf"
