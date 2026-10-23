from __future__ import annotations

import logging
import os
import re
import requests

from .config import load_config
from .models import ClientIntake, OpportunityScore, ValidationIssue

logger = logging.getLogger(__name__)


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


# Section headings for the list-style answers. Numbers are optional, so both
# the template's "## 3. Pain Points" and a hand-written "## Pain points" (or
# "### 4) Goals & outcomes") are recognised.
PAIN_POINT_HEADING = r"pain[\s-]*points?|challenges|problems"
GOAL_HEADING = r"goals?|objectives|desired outcomes"

# Compliance regimes recognised in the compliance answer (and notes).
COMPLIANCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("PHIPA", r"\bphipa\b"),
    ("HIPAA", r"\bhipaa\b"),
    ("PIPEDA", r"\bpipeda\b"),
    ("GDPR", r"\bgdpr\b"),
    ("SOC 2", r"\bsoc\s*-?\s*(?:2|ii|two)\b"),
    ("PCI DSS", r"\bpci(?:[\s-]*dss)?\b"),
    ("ISO 27001", r"\biso[\s/-]*27001\b"),
)

# Start-date phrases that mean "now". Matched as whole words/phrases.
URGENT_START_PATTERN = re.compile(
    r"\b(asap|a\.s\.a\.p|urgent(?:ly)?|immediately|right away|right now|"
    r"as soon as possible|this week|next week|this month)\b",
    re.IGNORECASE,
)

TIER_PRICE_BOUNDS = {
    "Quick Win": (500, 2500),
    "Custom AI Agent": (2500, 10000),
    "Full Integration": (10000, 25000),
}
TOP_TIER = "Full Integration"


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
        pain_points=_extract_topic_answers(content, PAIN_POINT_HEADING),
        goals=_extract_topic_answers(content, GOAL_HEADING),
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
    regimes = detect_compliance(intake)
    if regimes:
        issues.append(
            ValidationIssue(
                code="compliance_review",
                message=(
                    f"{', '.join(regimes)} requirements noted: confirm data handling, "
                    "hosting region and any vendor review before quoting."
                ),
            )
        )
    return issues


def blocking_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Issues that must be resolved before a proposal can be sent."""
    return [issue for issue in issues if issue.severity == "error"]


def detect_compliance(intake: ClientIntake) -> list[str]:
    """Named compliance regimes found in the compliance answer or notes."""
    text = f"{intake.compliance} {intake.notes}".lower()
    return [name for name, pattern in COMPLIANCE_PATTERNS if re.search(pattern, text)]


def is_urgent_start(start_date: str) -> bool:
    return bool(URGENT_START_PATTERN.search(start_date or ""))


def score_opportunity(intake: ClientIntake) -> OpportunityScore:
    pain_count = len(intake.pain_points)
    goal_count = len(intake.goals)
    has_tools = bool(intake.tools.strip())

    # No parsed pain point is an error-level gap: never recommend more than
    # the smallest, cheapest tier on that basis.
    if pain_count == 0 or (pain_count == 1 and not has_tools):
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

    urgent = is_urgent_start(intake.start_date)
    urgency = min(5, max(1, pain_count) + (2 if urgent else 0))
    budget_fit, budget_note = _assess_budget(intake.budget, tier)
    tech_readiness = _score_tech_readiness(intake.tech_person, intake.tools)
    strategic_value = 3
    total_score = urgency + budget_fit + tech_readiness + strategic_value

    if pain_count == 0:
        tier_reason = (
            "No pain points parsed, so the tier is provisional: Quick Win until "
            "the core problem is confirmed."
        )
    else:
        tier_reason = f"{pain_count} pain point(s) and {goal_count} goal(s) mapped to {tier}."
    reasons = [tier_reason]
    if urgent:
        reasons.append(
            f"Urgency raised for an immediate start ({intake.start_date.strip()})."
        )
    reasons.append(
        f"Budget fit scored {budget_fit}/5 from stated range: "
        f"{intake.budget or 'not provided'}{budget_note}."
    )
    reasons.append(
        f"Technical readiness scored {tech_readiness}/5 from tools and in-house tech signal."
    )
    regimes = detect_compliance(intake)
    if regimes:
        reasons.append(
            f"Compliance ({', '.join(regimes)}) adds a data-handling review to "
            "discovery; confirm before quoting."
        )
    reasons.append("Strategic value defaults to 3/5 until a human reviews portfolio fit.")

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


def generate_documents(intake: ClientIntake, score: OpportunityScore, strategic_analysis: str | None = None) -> dict[str, str]:
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
        "proposal-draft.md": _proposal_draft(intake, score),
    }


def _extract_table_rows(content: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")) or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "question":
            continue
        question = cells[0]
        answer = " | ".join(cells[1:])
        rows.append((question, answer))
    return rows


def _extract_section_answers(content: str, start_heading: str, end_heading_prefix: str) -> list[str]:
    """Backward-compatible wrapper: answers under the heading named ``start_heading``.

    The end marker is ignored; a section now ends at the next heading of the
    same or higher level, which is what every template uses.
    """
    del end_heading_prefix
    topic = re.sub(r"^\d+\s*[.)]?\s*", "", start_heading.strip())
    return _extract_topic_answers(content, re.escape(topic))


_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")


def _extract_topic_answers(content: str, topic_pattern: str) -> list[str]:
    """Table answers under the first heading whose title matches ``topic_pattern``.

    Accepts optional numbering ("3.", "3)", "3 -") and any heading level from
    ``##`` down; the section runs until the next heading at the same or a
    higher level (deeper sub-headings stay inside the section).
    """
    title_re = re.compile(
        rf"^(?:\d+\s*[.):-]?\s*)?(?:{topic_pattern})\b", re.IGNORECASE
    )
    lines = content.splitlines()
    start_index = None
    level = 0
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) >= 2 and title_re.match(match.group(2)):
            start_index, level = index + 1, len(match.group(1))
            break
    if start_index is None:
        return []

    section: list[str] = []
    for line in lines[start_index:]:
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) <= level:
            break
        section.append(line)

    answers = []
    for _, answer in _extract_table_rows("\n".join(section)):
        if answer and answer.lower() not in {"your answer", "n/a", "none"}:
            answers.append(answer)
    return answers


_AMOUNT_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<suffix>k|m|mm|thousand|million)?\b(?P<plus>\s*\+)?",
    re.IGNORECASE,
)
_OPEN_ABOVE_RE = re.compile(r"\b(over|above|more than|at least|min(?:imum)?|from|upwards of|\d\s*\+)", re.IGNORECASE)
_OPEN_BELOW_RE = re.compile(r"\b(under|below|less than|up to|max(?:imum)?|no more than|at most|<)", re.IGNORECASE)


def parse_budget(budget: str) -> tuple[float, float] | None:
    """Parse a free-text budget into a (low, high) dollar range.

    Handles "$5,000", "about 5k", "5-10k" (the suffix applies to both ends),
    "$5k-$10k", "1.5k", "$1M", "$50,000+" / "over 50k" (open-ended high) and
    "under 5k" / "up to $10,000" (open-ended low). Returns None when no
    amount is present. High is ``inf`` for open-ended budgets.
    """
    text = (budget or "").replace("\u2013", "-").replace("\u2014", "-").replace(",", "")
    if not text.strip():
        return None
    amounts: list[float] = []
    suffixes: list[str | None] = []
    plus = False
    for match in _AMOUNT_RE.finditer(text):
        value = float(match.group("num"))
        suffix = (match.group("suffix") or "").lower() or None
        amounts.append(value)
        suffixes.append(suffix)
        plus = plus or bool(match.group("plus"))
    if not amounts:
        return None

    # "5-10k": a trailing suffix applies to bare numbers before it.
    trailing = next((sfx for sfx in reversed(suffixes) if sfx), None)
    multipliers = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6}
    values = []
    for value, suffix in zip(amounts, suffixes):
        effective = suffix or (trailing if trailing and value < 1000 else None)
        values.append(value * multipliers.get(effective or "", 1))

    low, high = min(values), max(values)
    if plus or _OPEN_ABOVE_RE.search(text):
        return low, float("inf")
    if _OPEN_BELOW_RE.search(text) and len(values) == 1:
        return 0.0, high
    return low, high


def _score_budget_fit(budget: str, tier: str) -> int:
    return _assess_budget(budget, tier)[0]


def _assess_budget(budget: str, tier: str) -> tuple[int, str]:
    """Score (1-5 scale, as before) how a stated budget fits the tier.

    - unknown / unparseable: 2
    - overlaps the tier's price band: 4 for Quick Win, 3 otherwise (unchanged)
    - above the band: the top tier scores 4 (more budget than the largest
      package); lower tiers score 3 when within 2x the band's top, else 2
      because the scope is probably under-specified
    - entirely below the band: 2
    Returns (score, note) where note is appended to the score reason.
    """
    parsed = parse_budget(budget)
    if parsed is None or tier not in TIER_PRICE_BOUNDS:
        return 2, ""
    low, high = parsed
    tier_low, tier_high = TIER_PRICE_BOUNDS[tier]
    if high < tier_low:
        return 2, " (below the tier's range)"
    if low <= tier_high:
        return (4 if tier == "Quick Win" else 3), ""
    if tier == TOP_TIER:
        return 4, " (above the tier's range; scope and price can grow)"
    if low <= 2 * tier_high:
        return 3, " (above the tier's range; room for added scope)"
    return 2, " (far above the tier's range; re-check scope)"


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


def _numbered_lines(items: list[str]) -> str:
    if not isinstance(items, (list, tuple, set)):
        if items is None:
            return "None listed."
        items = [str(items)]
    if not items:
        return "None listed."
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _bullet_lines(items: list[str]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item}" for item in items)


def _as_text_list(items: object) -> list[str]:
    """Tolerate malformed list fields (None, scalars) the way _numbered_lines does."""
    if isinstance(items, (list, tuple)):
        return [str(item) for item in items if str(item).strip()]
    if items is None or isinstance(items, bool) or not str(items).strip():
        return []
    return [str(items)]


def _quote(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text.strip()).rstrip(".")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "\u2026"
    return text


def _company_line(intake: ClientIntake) -> str:
    name = intake.company_name.strip() or "The client"
    details = []
    if intake.industry.strip():
        details.append(intake.industry.strip())
    if intake.size.strip():
        size = intake.size.strip()
        details.append(f"{size} people" if size.isdigit() else size)
    if intake.location.strip():
        details.append(intake.location.strip())
    return f"**{name}**" + (f" ({'; '.join(details)})" if details else "")


def _proposal_draft(intake: ClientIntake, score: OpportunityScore) -> str:
    """Deterministic, intake-specific proposal draft (no AI).

    Every sentence is assembled from the parsed intake fields and the score,
    so two different clients never get the same summary. When the intake has
    an error-level issue the draft is replaced by a hold notice instead.
    """
    company = intake.company_name.strip() or "Your Company"
    issues = validate_intake(intake)
    blocking = blocking_issues(issues)
    if blocking:
        # Same section layout as a normal draft (the UI and eval rely on it),
        # but every section says the draft is on hold and why.
        return f"""# Proposal Draft: {company}

## Executive Summary
**On hold - not ready to send.** {_company_line(intake)} has blocking intake issue(s) that must be resolved first:
{_bullet_lines([issue.message for issue in blocking])}

## Recommended Solution
**{score.tier}** (provisional) - {score.scope}

## Delivery Plan
- Confirm the core problem with the client, then re-run the intake.

## Investment
- **Estimated Range:** {score.price_range} (provisional)
- **Timeline:** {score.timeline} (provisional)

## Next Step
Ask the client to describe the main problem they want solved, then re-run the intake.
"""

    regimes = detect_compliance(intake)
    pains = _as_text_list(intake.pain_points)
    goals = _as_text_list(intake.goals)
    summary = [f"Prepared for {_company_line(intake)}."]
    if pains:
        summary.append(f"Main problem: \u201c{_quote(pains[0])}.\u201d")
    if len(pains) > 1:
        extra = len(pains) - 1
        summary.append(
            f"Also noted: {extra} more pain point{'s' if extra > 1 else ''}, listed in the analysis."
        )
    if goals:
        summary.append(f"Success looks like: \u201c{_quote(goals[0])}.\u201d")
    tools = intake.tools.strip()
    summary.append(
        f"We recommend a **{score.tier}**: {score.scope[0].lower() + score.scope[1:]}"
        + (f" It works alongside their current tools ({tools})." if tools else "")
    )
    constraints = []
    if regimes:
        constraints.append(f"compliance ({_quote(intake.compliance or ', '.join(regimes), 120)})")
    if intake.start_date.strip():
        constraints.append(f"requested start: {intake.start_date.strip()}")
    if intake.budget.strip():
        constraints.append(f"stated budget: {intake.budget.strip()}")
    if constraints:
        summary.append("Constraints: " + "; ".join(constraints) + ".")

    phase1 = "Confirm success metrics"
    if goals:
        phase1 += f" for \u201c{_quote(goals[0], 120)}\u201d"
    if regimes:
        phase1 += (
            f", close any open intake questions, and complete the "
            f"{', '.join(regimes)} data-handling review."
        )
    else:
        phase1 += " and close any open intake questions."
    phase2 = "Build the workflow"
    if pains:
        phase2 += f" that removes \u201c{_quote(pains[0], 120)}\u201d"
    if tools:
        phase2 += f", integrated with {tools}"
    phase2 += "."
    phase3 = (
        "Pilot with the team, measure against the success metric, and hand off "
        "operating notes"
        + (f" to {intake.decision_maker.strip()}" if intake.decision_maker.strip() else "")
        + "."
    )

    open_items = [issue.message for issue in issues if issue.code != "compliance_review"]
    if open_items:
        next_step = "Before kickoff, confirm:\n" + _bullet_lines(open_items)
    elif intake.decision_maker.strip():
        next_step = f"Review this draft with {intake.decision_maker.strip()} and agree a kickoff date."
    else:
        next_step = "Review this draft with the client and agree a kickoff date."

    return f"""# Proposal Draft: {company}

## Executive Summary
{" ".join(summary)}

## Recommended Solution
**{score.tier}** - {score.scope}

## Delivery Plan
- **Phase 1:** {phase1}
- **Phase 2:** {phase2}
- **Phase 3:** {phase3}

## Investment
- **Estimated Range:** {score.price_range}
- **Timeline:** {score.timeline}

## Next Step
{next_step}
"""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def refine_with_jules(draft_content: str, target_persona: str) -> str:
    """
    Submits draft content to the Jules REST API for enterprise-tone refinement.
    If JULES_API_KEY is not set, logs a warning and returns the original draft content.
    Handles network errors, timeouts, invalid status codes, malformed JSON, and missing keys.
    """
    config = load_config()
    jules_api_key = config.get("JULES_API_KEY")
    if not jules_api_key or not str(jules_api_key).strip():
        logger.warning("JULES_API_KEY is not set or empty. Skipping Jules refinement.")
        return draft_content

    url = "https://jules.google/api/v1/refine"
    headers = {
        "Authorization": f"Bearer {jules_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "task": "evaluate_and_refine",
        "context": f"Target Audience: {target_persona}. Ensure strict enterprise tone.",
        "content": draft_content,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Jules API returned status code {response.status_code}. Falling back to original content.")
            return draft_content
        
        try:
            data = response.json()
        except ValueError:
            logger.warning("Jules API response was not valid JSON. Falling back to original content.")
            return draft_content

        if not isinstance(data, dict) or "refined_content" not in data:
            logger.warning("Jules API response does not contain 'refined_content' key. Falling back to original content.")
            return draft_content

        return data["refined_content"]

    except requests.exceptions.Timeout as e:
        logger.warning(f"Jules API request timed out: {e}. Falling back to original content.")
        return draft_content
    except requests.exceptions.RequestException as e:
        logger.warning(f"Jules API request failed: {e}. Falling back to original content.")
        return draft_content


def _resolve_obs_credentials() -> tuple[str, int, str | None] | None:
    config = load_config()
    
    host = os.environ.get("OBS_HOST")
    if host is None:
        host = config.get("SERVER_IP", "127.0.0.1")
        
    port_str = os.environ.get("OBS_PORT")
    if port_str is None:
        port = config.get("SERVER_PORT", 4455)
    else:
        try:
            port = int(port_str)
        except ValueError:
            port = config.get("SERVER_PORT", 4455)
            if not isinstance(port, int):
                try:
                    port = int(port)
                except ValueError:
                    port = 4455

    password = os.environ.get("OBS_PASSWORD")
    if password is None:
        password = config.get("SERVER_PASSWORD")

    # Specifically check if host is an empty string ""
    if host == "" or os.environ.get("OBS_HOST") == "":
        return None

    # Validate that the port is within the valid TCP range (1 to 65535, inclusive)
    if not (1 <= port <= 65535):
        return None
        
    return host, port, password


def trigger_obs_screenshot(file_path: str = "C:\\AI\\error_capture.png") -> str:
    """
    Triggers a screenshot in OBS using either obsws_python or obswebsocket.
    """
    creds = _resolve_obs_credentials()
    if not creds:
        logger.info("OBS credentials explicitly missing or empty. Skipping screenshot.")
        return "OBS Screenshot skipped: Missing credentials."
    
    host, port, password = creds

    # Try obsws_python first
    try:
        import obsws_python
        logger.info(f"Connecting to OBS via obsws_python on {host}:{port}")
        client = obsws_python.ReqClient(host=host, port=port, password=password)
        try:
            client.save_source_screenshot(
                os.environ.get("OBS_SOURCE_NAME", "Display Capture"),
                "png",
                file_path,
                int(os.environ.get("OBS_SCREENSHOT_WIDTH", "1920")),
                int(os.environ.get("OBS_SCREENSHOT_HEIGHT", "1080")),
                -1,
            )
            logger.info(f"OBS screenshot saved to {file_path}")
            return f"OBS Screenshot saved to {file_path}"
        finally:
            pass
    except ImportError:
        # Try obswebsocket next
        try:
            import obswebsocket
            from obswebsocket import obsws, requests
            logger.info(f"Connecting to OBS via obswebsocket on {host}:{port}")
            client = obsws(host, port, password)
            client.connect()
            try:
                client.call(requests.SaveSourceScreenshot(sourceName="Display Capture", imageFormat="png", imageFilePath=file_path))
                logger.info(f"OBS screenshot saved to {file_path} via obswebsocket")
                return f"OBS Screenshot saved to {file_path}"
            finally:
                client.disconnect()
        except ImportError:
            logger.error("No compatible OBS WebSocket library found (neither obsws_python nor obswebsocket).")
            return "OBS Screenshot failed: No compatible OBS WebSocket library found."
        except Exception as e:
            logger.error(f"OBS Screenshot failed under obswebsocket: {e}")
            return f"OBS Screenshot failed: {e}"
    except Exception as e:
        logger.error(f"OBS Screenshot failed under obsws_python: {e}")
        return f"OBS Screenshot failed: {e}"


def save_obs_replay_buffer() -> str:
    """
    Saves the OBS Replay Buffer using either obsws_python or obswebsocket.
    """
    creds = _resolve_obs_credentials()
    if not creds:
        logger.info("OBS credentials explicitly missing or empty. Skipping replay buffer.")
        return "OBS Replay Buffer skipped: Missing credentials."
    
    host, port, password = creds

    # Try obsws_python first
    try:
        import obsws_python
        logger.info(f"Connecting to OBS via obsws_python on {host}:{port}")
        client = obsws_python.ReqClient(host=host, port=port, password=password)
        try:
            client.save_replay_buffer()
            logger.info("OBS replay buffer saved.")
            return "OBS Replay Buffer saved."
        finally:
            pass
    except ImportError:
        # Try obswebsocket next
        try:
            import obswebsocket
            from obswebsocket import obsws, requests
            logger.info(f"Connecting to OBS via obswebsocket on {host}:{port}")
            client = obsws(host, port, password)
            client.connect()
            try:
                client.call(requests.SaveReplayBuffer())
                logger.info("OBS replay buffer saved via obswebsocket.")
                return "OBS Replay Buffer saved."
            finally:
                client.disconnect()
        except ImportError:
            logger.error("No compatible OBS WebSocket library found (neither obsws_python nor obswebsocket).")
            return "OBS Replay Buffer failed: No compatible OBS WebSocket library found."
        except Exception as e:
            logger.error(f"OBS Replay Buffer failed under obswebsocket: {e}")
            return f"OBS Replay Buffer failed: {e}"
    except Exception as e:
        logger.error(f"OBS Replay Buffer failed under obsws_python: {e}")
        return f"OBS Replay Buffer failed: {e}"

