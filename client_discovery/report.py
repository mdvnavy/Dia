"""Formatted PDF intake report (WeasyPrint + a Jinja template).

The template lives in templates/report/ (intake-report.html + intake-report.css)
and receives these placeholders: client, date, tier (letter), score, score_max, profile_html,
analysis_html, proposal_html and missing (a list of strings). Extra context
(score_detail, issues, intake, reasons, price_range, timeline) is also passed
for richer templates.

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


def build_context(result: dict[str, Any], *, report_date: str | None = None) -> dict[str, Any]:
    intake = result.get("intake") or {}
    score = result.get("score") or {}
    documents = result.get("documents") or {}
    issues = result.get("issues") or []
    context: dict[str, Any] = {
        "corners": REPORT_CORNERS,
        "client": str(intake.get("company_name") or "Untitled client"),
        "date": report_date or _date.today().strftime("%B %-d, %Y"),
        "tier": TIER_LETTERS.get(str(score.get("tier") or ""), str(score.get("tier") or "")[:1].upper()),
        "tier_name": str(score.get("tier") or ""),
        "score": str(score.get("total_score", 0)),
        "score_max": str(score.get("max_score", 20)),
        "missing": [str(issue.get("message", issue)) if isinstance(issue, dict) else str(issue) for issue in issues],
        "score_detail": score,
        "reasons": list(score.get("reasons") or []),
        "price_range": str(score.get("price_range") or ""),
        "timeline": str(score.get("timeline") or ""),
        "issues": issues,
        "intake": intake,
    }
    for key, doc_name in DOC_KEYS.items():
        context[key] = markdown_to_html(str(documents.get(doc_name, "")))
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
    context["font_dir"] = FONT_DIR.resolve().as_uri()
    return template.render(**context)


def render_pdf(result: dict[str, Any], *, report_date: str | None = None) -> bytes:
    from weasyprint import HTML

    document = HTML(
        string=render_html(result, report_date=report_date),
        base_url=TEMPLATE_DIR.resolve().as_uri() + "/",
        url_fetcher=_safe_fetcher(),
    )
    return document.write_pdf()


def pdf_filename(result: dict[str, Any]) -> str:
    client = str((result.get("intake") or {}).get("company_name") or "intake")
    slug = re.sub(r"[^a-z0-9]+", "-", client.lower()).strip("-") or "intake"
    return f"dia-{slug}.pdf"
