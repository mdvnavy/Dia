from __future__ import annotations

import re
from typing import TYPE_CHECKING
from client_discovery.models import ClientIntake, OpportunityScore

if TYPE_CHECKING:
    from .schema import DomainManifest


def _score_budget_fit(budget: str, tier: str) -> int:
    normalized = budget.replace("–", "-").lower()
    if not normalized.strip():
        return 2

    normalized_no_commas = normalized.replace(",", "")
    numbers = [int(x) for x in re.findall(r"\d+", normalized_no_commas)]

    if not numbers:
        return 2

    if tier == "Quick Win":
        if any(500 <= n <= 2500 for n in numbers):
            return 4
    elif tier == "Custom AI Agent":
        if any(2500 <= n <= 10000 for n in numbers):
            return 3
    elif tier == "Full Integration":
        if any(10000 <= n <= 25000 for n in numbers):
            return 3

    return 2


def _score_tech_readiness(tech_person: str, tools: str) -> int:
    tp_lower = tech_person.strip().lower()
    negative_answers = {"no", "none", "n/a", "not yet", "maybe", "tbd", "to be determined"}
    if tp_lower in negative_answers:
        return 2

    unready_keywords = {"", "no", "none", "n/a", "not yet", "maybe", "tbd", "to be determined"}
    has_tech_owner = tp_lower not in unready_keywords
    has_tools = bool(tools.strip())
    if has_tech_owner and has_tools:
        return 4
    if has_tech_owner or has_tools:
        return 3
    return 2


def score_opportunity(intake: ClientIntake, manifest: DomainManifest) -> OpportunityScore:
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
    strategic_value = manifest.scoring.get("strategic_value", 3)
    max_score = manifest.scoring.get("max_score", 20)
    total_score = urgency + budget_fit + tech_readiness + strategic_value

    reasons = [
        f"{pain_count} pain point(s) and {goal_count} goal(s) mapped to {tier}.",
        f"Budget fit scored {budget_fit}/5 from stated range: {intake.budget or 'not provided'}.",
        f"Technical readiness scored {tech_readiness}/5 from tools and in-house tech signal.",
        f"Strategic value defaults to {strategic_value}/5 until a human reviews portfolio fit.",
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
        max_score=max_score,
        reasons=reasons,
    )
