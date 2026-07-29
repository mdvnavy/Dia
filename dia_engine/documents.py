from __future__ import annotations

from typing import TYPE_CHECKING
from client_discovery.models import ClientIntake, OpportunityScore
from .core import _bullet_lines, _numbered_lines

if TYPE_CHECKING:
    from .schema import DomainManifest


def generate_documents(
    intake: ClientIntake,
    score: OpportunityScore,
    manifest: DomainManifest,
    strategic_analysis: str | None = None,
) -> dict[str, str]:
    pain_lines = _numbered_lines(intake.pain_points)
    goal_lines = _numbered_lines(intake.goals)

    opportunity_analysis = f"""# Opportunity Analysis: {intake.company_name or 'Unknown'}

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
"""
    if strategic_analysis:
        opportunity_analysis += f"\n\n{strategic_analysis}"

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
        "opportunity-analysis.md": opportunity_analysis,
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
