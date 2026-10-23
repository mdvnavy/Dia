from __future__ import annotations

import logging
import os
import re
import requests

from .config import load_config
from .models import ClientIntake, OpportunityScore, ValidationIssue

logger = logging.getLogger(__name__)


# Section 6 aliases come first; none of them is a substring of a section 1-5 question, and vice versa.
SECTION6_ALIASES = {
    "workflow_tasks": ("manual tasks",),
    "hourly_cost": ("hourly cost", "salary band", "loaded rate"),
    "success_metric": ("the one number", "success metric"),
    "process_owner": ("owns the process", "process owner"),
    "systems_access": ("systems involved", "login or api access"),
    "data_readiness": ("where does the data live", "data live", "how clean"),
    "exceptions": ("what goes wrong today", "exceptions and hand-offs"),
    "sensitive_data": ("regulated or sensitive",),
    "past_attempts": ("previous ai", "automation attempts"),
    "maintenance": ("who maintains",),
    "finance_signals": ("finance only", "days overdue"),
}

QUESTION_ALIASES = {
    **SECTION6_ALIASES,
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
        **{field: values.get(field, "") for field in SECTION6_ALIASES},
        workflows_section=bool(re.search(r"^##\s*6\.?\s", content, re.M)) or any(values.get(f) for f in SECTION6_ALIASES),
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
    # Section 6 Q1-Q3 are required, but only on questionnaires that carry section 6: older intakes without it
    # keep their issue list, and the scorer flags the gaps instead (VALUE_AT_STAKE_MISSING, NO_SUCCESS_METRIC).
    if intake.workflows_section:
        for field, code, message in (
            ("workflow_tasks", "missing_workflow_tasks", "Top manual tasks with weekly hours are needed for the value at stake."),
            ("hourly_cost", "missing_hourly_cost", "An hourly cost or salary band is needed for the value at stake."),
            ("success_metric", "missing_success_metric", "A success metric is needed to show the project worked."),
        ):
            if not getattr(intake, field).strip():
                issues.append(ValidationIssue(code=code, message=message))
    if not intake.goals:
        issues.append(
            ValidationIssue(
                code="missing_goals",
                message="At least one goal is required to generate a useful proposal.",
            )
        )
    return issues


# --- Scorer v2 (dia-savant v1.0 §2.3). Every threshold is a tunable starting point, not a research finding. ---
SCORING_RULES_VERSION = "2"


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def value_weeks() -> float:
    """Working weeks a year in the value-at-stake formula (default 48; DIA_VALUE_WEEKS overrides)."""
    return _env_number("DIA_VALUE_WEEKS", 48)


def value_bands() -> tuple[int, int, int]:
    """$/yr cut-offs for strategic value 3, 4 and 5 (default $5K/$20K/$50K; DIA_VALUE_BANDS="5000,20000,50000")."""
    raw = os.environ.get("DIA_VALUE_BANDS", "")
    try:
        low, mid, high = (int(float(x)) for x in raw.split(","))
        return (low, mid, high)
    except ValueError:
        return (5000, 20000, 50000)


def strategic_value_for(value: int | None) -> int:
    """Value at stake -> strategic value: >= high -> 5; >= mid -> 4; >= low -> 3; below -> 2; missing -> 3."""
    if value is None:
        return 3
    low, mid, high = value_bands()
    if value >= high:
        return 5
    if value >= mid:
        return 4
    if value >= low:
        return 3
    return 2


_NUMBER = r"\d[\d,]*(?:\.\d+)?"


def _to_number(text: str) -> float:
    return float(text.replace(",", ""))


def parse_hourly_rate(text: str) -> float | None:
    """Q2: '$55 an hour', '$40/hr', or a salary band ('$60,000-$80,000 a year', '$60K') -> $/hour.
    A salary (or band midpoint) is divided by 2,080 hours; no loading multiplier is added."""
    t = (text or "").lower().replace("–", "-")
    found = [(_to_number(m.group(1)) * (1000 if m.group(2) else 1)) for m in re.finditer(rf"({_NUMBER})\s*(k\b)?", t)]
    if not found:
        return None
    if re.search(r"/\s*h|\bhour|\bhr\b|\bhrs?\b|hourly|per hour", t) and found[0] < 1000:
        return found[0]
    annual = [n for n in found if n >= 1000]
    if annual:
        return (sum(annual[:2]) / len(annual[:2])) / 2080
    return found[0] if found[0] > 0 else None


def _workflow_parts(text: str) -> list[str]:
    """Q1 split into its task entries (semicolons or new lines), blanks and placeholders dropped, at most three."""
    parts = []
    for part in re.split(r";|<br\s*/?>|\n", text or ""):
        part = part.strip()
        if part and part.lower() not in {"n/a", "none", "your answer"}:
            parts.append(part)
    return parts[:3]


def parse_workflows(text: str) -> list[dict]:
    """Q1: up to three tasks separated by semicolons or new lines: 'Task - who, 6 hours a week, 2 people'.
    The hours are the task's total hours a week (the people count is informational)."""
    rows = []
    for part in _workflow_parts(text):
        task = re.split(r"\s+[-–—]\s+|:|\(|,", part, maxsplit=1)[0].strip() or part
        hours = re.search(rf"({_NUMBER})\s*(?:h\b|hrs?\b|hours?\b)", part.lower())
        people = re.search(r"(\d+)\s*(?:people|persons?|staff)\b", part.lower())
        rows.append({
            "task": task,
            "hours": _to_number(hours.group(1)) if hours else None,
            "people": int(people.group(1)) if people else None,
        })
    return rows


TIME_SINK_KEYWORDS = {  # §2.1 categories; lead intake and follow-up (Low confidence) is left out on purpose
    "finance admin": ("invoice", "invoicing", "bookkeeping", "accounting", "reconcil", "payroll", "billing", "expense"),
    "coordination": ("coordinat", "status update", "meeting", "scheduling", "hand-off", "handoff", "chasing"),
    "owner admin": ("admin", "firefight", "context switch", "paperwork"),
    "compliance paperwork": ("compliance", "regulatory filing", "audit prep", "reporting to the regulator"),
}


def time_sink_matches(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [name for name, words in TIME_SINK_KEYWORDS.items() if any(word in lowered for word in words)]


def top_time_sink(text: str, values: list[float | None]) -> str | None:
    """The time sink to name in the urgency reason: the category of the highest-value Q1 task that matches one.
    `values` is the per-task value at stake, in Q1 order (as parse_workflows returns the tasks). Only the task text
    before " - " is matched, so the person named after it never picks the category. A task that matches
    more than one category is named by the first in keyword order, and tied values fall back to keyword order.
    When no matching task has a value, this is the first category matched anywhere in Q1 (the keyword-order rule)."""
    sinks = time_sink_matches(text)
    if not sinks:
        return None
    order = list(TIME_SINK_KEYWORDS)
    candidates = []
    for part, value in zip(_workflow_parts(text), values):
        # Match the task itself, not the "who" after " - ": a "sales coordinator" doing lead triage is not coordination.
        matched = time_sink_matches(re.split(r"\s+[-–—]\s+", part, maxsplit=1)[0])
        if matched and value is not None:
            candidates.append((-value, order.index(matched[0]), matched[0]))
    return min(candidates)[2] if candidates else sinks[0]


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _blank(text: str) -> bool:
    return (text or "").strip().lower() in {"", "n/a", "none", "tbd", "your answer", "-"}


def _data_not_ready(text: str) -> bool:
    t = (text or "").lower()
    explicit = re.search(r"(?<![\d.])([1-5])\s*(?:/|of|out of)\s*5\b", t)
    lone = re.findall(r"(?<![\d.,$%])\b([1-5])\b(?![\d.,%])", t)
    rating = int(explicit.group(1)) if explicit else (int(lone[-1]) if lone else None)
    return (rating is not None and rating <= 2) or bool(re.search(r"spreadsheet|excel|google sheets|free[- ]text", t))


def _systems_risk(text: str) -> bool:
    return bool(re.search(r"\bno (?:login|api|access)\b|no access|cannot grant|can't grant|can not grant|"
                          r"not able to grant|never used|not sure|unknown", (text or "").lower()))


def _failed_attempt(text: str) -> bool:
    t = (text or "").lower()
    return not _blank(t) and bool(re.search(r"fail|did not work|didn't work|abandon|scrapp|stopped using|gave up|"
                                            r"cancel|went nowhere|no result|never launched|shut down", t))


def _exception_count(text: str) -> int:
    if _blank(text):
        return 0
    return len([item for item in re.split(r"[;,\n]", text) if item.strip()])


def _overdue_share(text: str) -> float | None:
    m = re.search(rf"({_NUMBER})\s*%", text or "")
    return _to_number(m.group(1)) if m else None


def _widen_timeline(timeline: str) -> str:
    m = re.match(r"(\d+)-(\d+) weeks", timeline)
    return f"{m.group(1)}-{int(m.group(2)) + 2} weeks" if m else timeline


def _budget_top(budget: str) -> int | None:
    """Top of a budget answer in dollars: '$5k-$10k' -> 10000, '$2.5K' -> 2500, '$1.2M' -> 1200000, '$5,000' -> 5000.
    Decimals stay whole and k/M suffixes are applied before the maximum is taken (Codex review on PR #39)."""
    t = (budget or "").lower().replace("–", "-").replace("—", "-")
    scale = {"k": 1_000, "m": 1_000_000}
    numbers = [_to_number(m.group(1)) * scale.get(m.group(2) or "", 1)
               for m in re.finditer(rf"({_NUMBER})\s*(k|m)?(?![a-z])", t)]
    return round(max(numbers)) if numbers else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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

    flags: list[str] = []
    scope_notes: list[str] = []
    quick_wins: list[str] = []

    # Urgency: pain points, +1 when the manual work matches a known time sink (capped at 5).
    # The named sink is the highest-value matching task's category (see top_time_sink), set once values are known.
    urgency = min(5, max(1, pain_count))
    urgency_reason = f"{_plural(pain_count, 'pain point')} listed"

    budget_fit = _score_budget_fit(intake.budget, tier)
    tech_readiness = _score_tech_readiness(intake.tech_person, intake.tools)
    readiness_bits = [
        "an in-house technical person and current tools" if tech_readiness == 4
        else "either an in-house technical person or current tools, not both" if tech_readiness == 3
        else "no in-house technical person or current tools"
    ]

    # Value at stake = sum over tasks of hours a week x loaded hourly rate x weeks.
    weeks = value_weeks()
    rate = parse_hourly_rate(intake.hourly_cost)
    workflows = []
    for row in parse_workflows(intake.workflow_tasks):
        value = round(row["hours"] * rate * weeks) if (row["hours"] is not None and rate) else None
        gaps = [gap for gap, missing in (("Weekly hours missing", row["hours"] is None), ("Hourly cost missing", not rate)) if missing]
        workflows.append({**row, "value": value, "gaps": gaps})
    sink = top_time_sink(intake.workflow_tasks, [row["value"] for row in workflows])
    if sink:
        urgency = min(5, urgency + 1)
        urgency_reason += f", and the manual work matches a known time sink ({sink})"
    workflows.sort(key=lambda row: (-(row["value"] or 0), len(row["gaps"])))
    # Ikenna (via Bash): 'show partial value as "at least $X"'. When some listed tasks have a value and others do
    # not, the subtotal of the valued tasks is a lower bound: it is shown as "at least $X", flagged
    # VALUE_AT_STAKE_PARTIAL (Inputs "thin"), and banded as a floor. Only when no task has a value is it missing.
    # Each valued row still shows its own figure in Top workflows.
    valued = [row["value"] for row in workflows if row["value"] is not None]
    value_partial = bool(valued) and len(valued) < len(workflows)
    value_at_stake = sum(valued) if valued else None
    strategic_value = strategic_value_for(value_at_stake)
    strategic_default = value_at_stake is None
    if strategic_default:
        flags.append("VALUE_AT_STAKE_MISSING")
        strategic_reason = "default until weekly hours and an hourly cost are provided"
    elif value_partial:
        flags.append("VALUE_AT_STAKE_PARTIAL")
        strategic_reason = (f"value at stake of at least ${value_at_stake:,} a year "
                            "(tasks without weekly hours are excluded)")
    else:
        strategic_reason = f"value at stake of about ${value_at_stake:,} a year"

    # Flags. A missing success metric is a flag only: it never caps or changes the tier.
    if not _has_number(intake.success_metric):
        flags.append("NO_SUCCESS_METRIC")
    if intake.workflows_section:  # Q4-Q11 checks apply only when the client was asked them
        if _blank(intake.process_owner):
            flags.append("NO_OWNER")
            tech_readiness -= 1
            readiness_bits.append("no process owner is named")
        if _data_not_ready(intake.data_readiness):
            flags.append("DATA_CLEANUP_FIRST")
            tech_readiness -= 1
            readiness_bits.append("the data needs clean-up first")
            scope_notes.append("Data preparation: clean and consolidate the source data before the build.")
        if _systems_risk(intake.systems_access):
            flags.append("SCOPE_RISK_SYSTEMS")
            timeline = _widen_timeline(timeline)
        if intake.sensitive_data.strip().lower().startswith("yes"):
            flags.append("COMPLIANCE_REVIEW")
        if _failed_attempt(intake.past_attempts):
            flags.append("TRUST_REPAIR")
            scope_notes.append("Pilot: a paid pilot on a single workflow, measured against one agreed metric.")
        if _exception_count(intake.exceptions) >= 3:
            flags.append("HUMAN_IN_LOOP")
            scope_notes.append("Hand-off rules: route each listed exception to a named person.")
        if _blank(intake.maintenance):
            flags.append("SUPPORT_TERMS_NEEDED")
        overdue = _overdue_share(intake.finance_signals)
        if overdue is not None and overdue >= 30:
            quick_wins.append("Accounts-receivable follow-up: automated reminders for invoices more than 30 days overdue.")
    tech_readiness = max(1, tech_readiness)

    # Budget against value: top of the budget range / value at stake.
    budget_reason = (f"stated budget of {intake.budget} against the {price_range} band" if intake.budget.strip()
                     else "no budget range was provided")
    top = _budget_top(intake.budget)
    if value_at_stake and top:
        ratio = top / value_at_stake
        if ratio < 1:
            budget_fit = min(5, budget_fit + 1)
            budget_reason += "; the yearly value at stake exceeds the top of the budget"
        elif ratio > 2 and not value_partial:  # a lower bound cannot show the budget outruns the value
            flags.append("ROI_WEAK")

    total_score = urgency + budget_fit + tech_readiness + strategic_value
    signal_reasons = {
        "urgency": urgency_reason,
        "budget_fit": budget_reason,
        "tech_readiness": "; ".join(readiness_bits),
        "strategic_value": strategic_reason,
    }
    reasons = [
        f"{_plural(pain_count, 'pain point')} and {_plural(goal_count, 'goal')} map to the {tier} tier.",
        f"Urgency {urgency}/5: {urgency_reason}.",
        f"Budget fit {budget_fit}/5: {budget_reason}.",
        f"Readiness {tech_readiness}/5: {signal_reasons['tech_readiness']}.",
        f"Strategic value {strategic_value}/5: {strategic_reason}.",
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
        value_at_stake=value_at_stake,
        value_at_stake_partial=value_partial,
        strategic_value_default=strategic_default,
        flags=flags,
        workflows=workflows,
        scope_notes=scope_notes,
        quick_wins=quick_wins,
        signal_reasons=signal_reasons,
        rules_version=SCORING_RULES_VERSION,
    )


# Persona v1.2 rule 1: no AI text in client exports. Gemini output (strategic_analysis, with or without
# Jules refinement) and its "DIA Agent Error" fallback are unreviewed drafts: never part of the documents.
# A future "add to export after review" step would append reviewed text explicitly; it doesn't exist yet.
UNREVIEWED_AI_KEYS = frozenset({"strategic_analysis"})
AI_ERROR_MARKER = "dia agent error"
_SCORE_REASONS = "\n## Score Reasons\n"  # pre-savant documents (stored cabinet results)
_TOP_WORKFLOWS = "\n## Top workflows by value at stake\n"  # dia-savant v1.0: the analysis document's last section


def _deterministic_analysis(text: str) -> str:
    """Cut opportunity-analysis.md after its last deterministic section, dropping anything appended to it.
    Savant documents end with "Top workflows by value at stake": its action title, then one block (the
    table rows or one sentence). Older documents end with the Score Reasons bullets."""
    head, sep, tail = text.partition(_TOP_WORKFLOWS)
    if sep:
        kept, seen_title, in_block = [], False, False
        for line in tail.split("\n"):
            if not seen_title:
                kept.append(line)
                seen_title = line.startswith("### ")
                continue
            if line.strip():
                in_block = True
                kept.append(line)
            elif in_block:
                break
            else:
                kept.append(line)
        return f"{head}{sep}" + "\n".join(kept).rstrip("\n") + "\n"
    head, sep, tail = text.partition(_SCORE_REASONS)
    if not sep:
        return text
    bullets = []
    for line in tail.split("\n"):
        if not line.startswith("- "):
            break
        bullets.append(line)
    return f"{head}{sep}" + "\n".join(bullets) + "\n"


def scrub_for_export(value, key: str | None = None):
    """Deep-copy a result (or documents) for export: drop unreviewed AI keys, cut appended text from
    opportunity-analysis.md, and remove any line carrying a DIA Agent Error, whatever produced it."""
    if isinstance(value, dict):
        return {k: scrub_for_export(v, k) for k, v in value.items() if k not in UNREVIEWED_AI_KEYS}
    if isinstance(value, list):
        return [scrub_for_export(v) for v in value]
    if isinstance(value, str):
        text = _deterministic_analysis(value) if key == "opportunity-analysis.md" else value
        return "\n".join(line for line in text.split("\n") if AI_ERROR_MARKER not in line.lower())
    return value


def generate_documents(intake: ClientIntake, score: OpportunityScore, *, report_date: str | None = None) -> dict[str, str]:
    """The three Markdown documents, rendered from the same answer-first report model as the PDF (§3, §6).
    Together, in the export order analysis, profile, proposal, they follow the PDF's section order."""
    from .report_model import build_report, render_documents

    return render_documents(build_report(intake, score, validate_intake(intake), report_date))


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

