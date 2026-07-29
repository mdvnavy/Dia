from __future__ import annotations

from typing import TYPE_CHECKING
from client_discovery.models import ClientIntake
from .core import _extract_section_answers, _extract_table_rows, _normalize

if TYPE_CHECKING:
    from .schema import DomainManifest


def parse_questionnaire_markdown(content: str, manifest: DomainManifest) -> ClientIntake:
    rows = _extract_table_rows(content)
    values: dict[str, str] = {}
    for question, answer in rows:
        normalized = _normalize(question)
        for field, aliases in manifest.question_aliases.items():
            if any(alias in normalized for alias in aliases):
                values[field] = answer.strip()
                break

    section_answers: dict[str, list[str]] = {}
    for section_key, config in manifest.section_queries.items():
        start = config.get("start_heading", "")
        end = config.get("end_heading_prefix", "")
        section_answers[section_key] = _extract_section_answers(content, start, end)

    return ClientIntake(
        company_name=values.get("company_name", ""),
        website=values.get("website", ""),
        industry=values.get("industry", ""),
        size=values.get("size", ""),
        years_in_business=values.get("years_in_business", ""),
        location=values.get("location", ""),
        tools=values.get("tools", ""),
        pain_points=section_answers.get("pain_points", []),
        goals=section_answers.get("goals", []),
        budget=values.get("budget", ""),
        decision_maker=values.get("decision_maker", ""),
        start_date=values.get("start_date", ""),
        tech_person=values.get("tech_person", ""),
        compliance=values.get("compliance", ""),
        notes=values.get("notes", ""),
    )
