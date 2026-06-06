from __future__ import annotations

import re

from .models import ClientIntake, OpportunityScore, ValidationIssue


QUESTION_ALIASES = {
    "company_name": ("company name",),
    "website": ("website",),
    "industry": ("industry / niche", "industry", "niche"),
    "size": ("company size", "size"),
    "years_in_business": ("years in business",),
    "location": ("location",),
    "tools": ("what tools/software do you use today?", "what tools/software do you use", "tools/software do you use", "tools"),
    "budget": ("budget range", "budget"),
    "decision_maker": ("final decision-maker", "decision maker", "decision-maker"),
    "start_date": ("preferred start date", "start date"),
    "tech_person": ("in-house tech person", "tech person"),
    "compliance": ("compliance requirements", "compliance"),
    "notes": ("anything else we should know", "notes"),
}


def parse_questionnaire_markdown(content: str) -> ClientIntake:
    rows = _extract_table_rows(content)
    values: dict[str, str] = {}
    for question, answer in rows:
        normalized = _normalize(question)
        for field, aliases in QUESTION_ALIASES.items():
            if any(alias in normalized for alias in aliases):
                values[field] = answer.strip()
                break

    return ClientIntake(
        company_name=values.get("company_name", ""),
        website=values.get("website", ""),
        industry=values.get("industry", ""),
        size=values.get("size", ""),
        years_in_business=values.get("years_in_business", ""),
        location=values.get("location", ""),
        tools=values.get("tools", ""),
        pain_points=_extract_section_answers(content, "3. Pain Points", "4. Goals"),
        goals=_extract_section_answers(content, "4. Goals", "5."),
        budget=values.get("budget", ""),
        decision_maker=values.get("decision_maker", ""),
        start_date=values.get("start_date", ""),
        tech_person=values.get("tech_person", ""),
        compliance=values.get("compliance", ""),
        notes=values.get("notes", ""),
    )


def validate_intake(intake: ClientIntake) -> list[ValidationIssue]:
    required_fields = {
        "company_name": "Company name is required for generated documents.",
        "budget": "Budget range is needed to check fit against the recommended tier.",
        "decision_maker": "Decision maker is needed before proposal handoff.",
        "start_date": "Preferred start date is needed for timeline fit.",
    }
    issues: list[ValidationIssue] = []
    for field, message in required_fields.items():
        if not getattr(intake, field).strip():
            issues.append(ValidationIssue(code=f"missing_{field}", message=message))

    if not intake.pain_points:
        issues.append(
            ValidationIssue(
                code="missing_pain_points",
                message="At least one pain point is required for scoring.",
                severity="error",
            )
        )
    if not intake.goals:
        issues.append(
            ValidationIssue(
                code="missing_goals",
                message="At least one goal is required to generate a useful proposal.",
            )
        )
    return issues


def score_opportunity(intake: ClientIntake) -> OpportunityScore:
    pain_count = len(intake.pain_points)
    goal_count = len(intake.goals)
    has_tools = bool(intake.tools.strip())

    if pain_count == 1 and not has_tools:
        tier = "Quick Win"
        scope = "One targeted automation with a narrow handoff."
        price_range = "$500-$2,500"
        timeline = "1-2 weeks"
    elif pain_count <= 3 and goal_count <= 3:
        tier = "Custom AI Agent"
        scope = "Purpose-built client workflow agent for the core bottleneck."
        price_range = "$2,500-$10,000"
        timeline = "2-4 weeks"
    else:
        tier = "Full Integration"
        scope = "System-wide automation connecting multiple tools and teams."
        price_range = "$10,000-$25,000"
        timeline = "4-8 weeks"

    urgency = min(5, max(1, pain_count))
    budget_fit = _score_budget_fit(intake.budget, tier)
    tech_readiness = _score_tech_readiness(intake.tech_person, intake.tools)
    strategic_value = 3
    total_score = urgency + budget_fit + tech_readiness + strategic_value
    reasons = [
        f"{pain_count} pain point(s) and {goal_count} goal(s) mapped to {tier}.",
        f"Budget fit scored {budget_fit}/5 from stated range: {intake.budget or 'not provided'}.",
        f"Technical readiness scored {tech_readiness}/5 from tools and in-house tech signal.",
        "Strategic value defaults to 3/5 until a human reviews portfolio fit.",
    ]

    return OpportunityScore(
        tier=tier,
        scope=scope,
        price_range=price_range,
        timeline=timeline,
        urgency=urgency,
        budget_fit=budget_fit,
        tech_readiness=tech_readiness,
        strategic_value=strategic_value,
        total_score=total_score,
        max_score=20,
        reasons=reasons,
    )


def generate_documents(intake: ClientIntake, score: OpportunityScore) -> dict[str, str]:
    pain_lines = _numbered_lines(intake.pain_points)
    goal_lines = _numbered_lines(intake.goals)

    return {
        "client-profile.md": f"""# Client Profile: {intake.company_name or 'Unknown'}

## Overview
- **Industry:** {intake.industry or 'TBD'}
- **Size:** {intake.size or 'TBD'}
- **Location:** {intake.location or 'TBD'}
- **Website:** {intake.website or 'TBD'}
- **Years in Business:** {intake.years_in_business or 'TBD'}

## Current Tools
{intake.tools or 'None listed.'}

## Decision Context
- **Decision Maker:** {intake.decision_maker or 'TBD'}
- **Preferred Start:** {intake.start_date or 'TBD'}
- **Compliance:** {intake.compliance or 'None listed'}

## Notes
{intake.notes or 'None provided.'}
""",
        "opportunity-analysis.md": f"""# Opportunity Analysis: {intake.company_name or 'Unknown'}

## Pain Points
{pain_lines}

## Goals
{goal_lines}

## Recommendation
- **Tier:** {score.tier}
- **Scope:** {score.scope}
- **Price Range:** {score.price_range}
- **Timeline:** {score.timeline}
- **Total Score:** {score.total_score}/{score.max_score}

## Score Reasons
{_bullet_lines(score.reasons)}
""",
        "proposal-draft.md": f"""# Proposal Draft: {intake.company_name or 'Your Company'}

## Executive Summary
{intake.company_name or 'The client'} needs a reliable way to turn discovery intake into a qualified opportunity package. The recommended path is a {score.tier} that converts questionnaire responses into a client profile, scored opportunity analysis, and proposal draft.

## Recommended Solution
**{score.tier}** - {score.scope}

## Delivery Plan
- **Phase 1:** Confirm intake fields, missing information, and success criteria.
- **Phase 2:** Configure the agent workflow and generated document templates.
- **Phase 3:** Run sample intakes, review outputs, and hand off operating notes.

## Investment
- **Estimated Range:** {score.price_range}
- **Timeline:** {score.timeline}

## Next Step
Review the generated opportunity analysis and confirm the missing fields before kickoff.
""",
    }


def _extract_table_rows(content: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "question":
            continue
        rows.append((cells[0], cells[1]))
    return rows


def _extract_section_answers(content: str, start_heading: str, end_heading_prefix: str) -> list[str]:
    start = re.search(rf"##\s*{re.escape(start_heading)}.*", content, re.IGNORECASE)
    if not start:
        return []
    section = content[start.start() :]
    end = re.search(rf"\n##\s*{re.escape(end_heading_prefix)}", section, re.IGNORECASE)
    if end:
        section = section[: end.start()]
    answers = []
    for _, answer in _extract_table_rows(section):
        if answer and answer.lower() not in {"your answer", "n/a", "none"}:
            answers.append(answer)
    return answers


def _score_budget_fit(budget: str, tier: str) -> int:
    normalized = budget.replace("–", "-").lower()
    if not normalized.strip():
        return 2
    if tier == "Quick Win" and any(token in normalized for token in ["500", "2,500", "2500"]):
        return 4
    if tier == "Custom AI Agent" and any(token in normalized for token in ["2,500", "2500", "10,000", "10000"]):
        return 3
    if tier == "Full Integration" and any(token in normalized for token in ["10,000", "10000", "25,000", "25000"]):
        return 3
    return 2


def _score_tech_readiness(tech_person: str, tools: str) -> int:
    has_tech_owner = tech_person.strip().lower() not in {"", "no", "none", "n/a"}
    has_tools = bool(tools.strip())
    if has_tech_owner and has_tools:
        return 4
    if has_tech_owner or has_tools:
        return 3
    return 2


def _numbered_lines(items: list[str]) -> str:
    if not items:
        return "None listed."
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _bullet_lines(items: list[str]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item}" for item in items)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
