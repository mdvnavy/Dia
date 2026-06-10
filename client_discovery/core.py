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
    def get_pattern(heading: str) -> str:
        m = re.match(r"^(\d+)\.(.*)$", heading)
        if m:
            return rf"{m.group(1)}\.?{re.escape(m.group(2))}"
        return re.escape(heading)

    start_pat = get_pattern(start_heading)
    end_pat = get_pattern(end_heading_prefix)

    start = re.search(rf"##\s*{start_pat}.*", content, re.IGNORECASE)
    if not start:
        return []
    section = content[start.start() :]
    end = re.search(rf"\n##\s*{end_pat}", section, re.IGNORECASE)
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

    # Remove commas to avoid splitting them or misparsing
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

