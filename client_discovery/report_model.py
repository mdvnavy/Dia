"""One deterministic report model for every export (dia-savant v1.0 §3, §5, §6).

build_report() turns an intake, its score and its validation issues into an answer-first report:
the Markdown documents (generate_documents) and the PDF (report.build_context) both render from it,
so the two can never disagree. Every sentence comes from a fixed template in this file: no AI-written
text, no research statistics, formal register (third person, no contractions, never "we").
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date as _date

from .core import QUESTION_NUMBERS
from .inputs import next_step_from_gap, next_steps, next_steps_markdown
from .models import ClientIntake, OpportunityScore, ValidationIssue

CLIENT_PRICING_TEXT = "Contact for pricing"  # matches the site; tier price bands never reach client output

# §5, verbatim. The Method and sources section prints this block exactly (only {Client} and {date} vary).
METHOD_AND_SOURCES = """METHOD
DIA scores each intake on four dimensions (urgency, budget fit, technical readiness,
strategic value), 0–5 each, 20 maximum. Value at stake is hours per week × loaded
hourly rate × 48 weeks, from the client's own answers. Tier and timeline come from
DIA's own rules. This report is generated from fixed templates; no AI-written
text appears in it.

SOURCES
Data: {client} discovery questionnaire, received {date}.
Presentation conventions informed by:
  · Minto, B. The Minto Pyramid Principle (answer-first structure).
  · Zelazny, G. Say It With Charts, 4th ed., McGraw-Hill, 2001.
  · Google, Material Design data visualization guidelines; People + AI Guidebook.
  · Microsoft Writing Style Guide; Apple Human Interface Guidelines.
The analysis and recommendation are DIA's own. These works shaped the layout, not the
conclusions. Their authors and publishers have not reviewed or endorsed DIA or this
report. Names are trademarks of their owners."""
# Added after the Method paragraph only when some tasks lack weekly hours (VALUE_AT_STAKE_PARTIAL).
METHOD_PARTIAL_NOTE = ("Tasks listed without weekly hours are excluded from the value at stake, so the figure\n"
                       "is a lower bound and is shown as \"at least\".")

def _weeks_label() -> str:
    """The week count the scorer actually used, so the Method text matches the dollar figures (Codex review on #39).
    The default (48) leaves the §5 text exactly as approved."""
    from .core import value_weeks

    weeks = value_weeks()
    return str(int(weeks)) if float(weeks).is_integer() else f"{weeks:g}"


WORKS = (
    "Minto, B. The Minto Pyramid Principle (answer-first structure).",
    "Zelazny, G. Say It With Charts, 4th ed., McGraw-Hill, 2001.",
    "Google, Material Design data visualization guidelines; People + AI Guidebook.",
    "Microsoft Writing Style Guide; Apple Human Interface Guidelines.",
)

# §3.1 order. key -> (label, Markdown document it lives in). The MD export joins analysis, profile, proposal.
SECTIONS = (
    ("summary", "Summary", "opportunity-analysis.md"),
    ("missing", "What is missing", "opportunity-analysis.md"),
    ("recommendation", "Recommendation", "opportunity-analysis.md"),
    ("why", "Why this tier", "opportunity-analysis.md"),
    ("workflows", "Top workflows by value at stake", "opportunity-analysis.md"),
    ("profile", "Client profile", "client-profile.md"),
    ("pains", "Pain points and goals", "client-profile.md"),
    ("delivery", "Delivery plan and investment", "proposal-draft.md"),
    ("method", "Method and sources", "proposal-draft.md"),
)
DOC_TITLES = {
    "opportunity-analysis.md": "Opportunity analysis",
    "client-profile.md": "Client profile",
    "proposal-draft.md": "Proposal draft",
}
MD_EXPORT_ORDER = ("opportunity-analysis.md", "client-profile.md", "proposal-draft.md")

# Answers the client still owes (counted in "What is missing"), with the issue codes they duplicate.
# Short labels stay at 2 words or fewer so the "What is missing" title never passes 15 words.
ANSWER_FLAGS = {
    "VALUE_AT_STAKE_MISSING": ("value inputs", ("missing_workflow_tasks", "missing_hourly_cost"),
                               "Weekly hours and an hourly cost are needed to compute the value at stake."),
    "VALUE_AT_STAKE_PARTIAL": ("task hours", (),
                               "Weekly hours are needed for every listed task; tasks without them are left out of the value at stake."),
    "NO_SUCCESS_METRIC": ("success metric", ("missing_success_metric",),
                          "A success metric with a number is needed to show the project worked."),
    "NO_OWNER": ("process owner", (), "A process owner is needed to run the project and sign it off."),
    "SUPPORT_TERMS_NEEDED": ("support terms", (), "Maintenance and support terms after launch are needed."),
}
ISSUE_LABELS = {
    "missing_company_name": "company name",
    "missing_budget": "budget range",
    "missing_decision_maker": "decision maker",
    "missing_start_date": "start date",
    "missing_pain_points": "pain points",
    "missing_goals": "goals",
    "missing_workflow_tasks": "manual tasks",
    "missing_hourly_cost": "hourly cost",
    "missing_success_metric": "success metric",
}
FLAG_ACTIONS = {  # §2.4: every flag is written as the action that clears it. Formal third person ({client}),
    # because these lines reach the client PDF (code review on #39: no operator voice such as "Ask for...").
    # Question references are {q:field[,field]} tokens, numbered from core.QUESTION_NUMBERS by flag_action() and
    # left out for questionnaires without section 6. Never write a literal "(question N)" here.
    "VALUE_AT_STAKE_MISSING": "{client} should provide the weekly hours of each manual task and the loaded hourly cost{q:workflow_tasks,hourly_cost}.",
    "VALUE_AT_STAKE_PARTIAL": "{client} should provide the weekly hours of each manual task that has none, to complete the value at stake{q:workflow_tasks}.",
    "NO_SUCCESS_METRIC": "{client} should agree on one success metric and its value today{q:success_metric}.",
    "NO_OWNER": "{client} should name the process owner and the person who signs off on the project{q:process_owner}.",
    "DATA_CLEANUP_FIRST": "{client} should plan data clean-up before the build starts{q:data_readiness}.",
    "SCOPE_RISK_SYSTEMS": ("{client} should confirm login or application programming interface (API) access to every "
                           "system before the timeline is fixed{q:systems_access}."),
    "COMPLIANCE_REVIEW": "A person should review every output before it is sent{q:sensitive_data}.",
    "TRUST_REPAIR": ("Given the earlier automation attempt{q:past_attempts}, the engagement should start with a paid pilot "
                     "on a single workflow, measured against one agreed metric{q:success_metric}."),
    "HUMAN_IN_LOOP": "{client} should write hand-off rules that route each listed exception to a named person{q:exceptions}.",
    "SUPPORT_TERMS_NEEDED": "{client} should agree on who maintains the system and what support follows launch{q:maintenance}.",
    "ROI_WEAK": "The budget should be checked against the value at stake: the top of the budget exceeds twice the yearly value.",
}
QUESTION_REF = re.compile(r"\{q:([a-z_,]+)\}")


def question_ref(fields: list[str], numbered: bool = True) -> str:
    """The ' (question N)' or ' (questions N and M)' suffix, numbered from core.QUESTION_NUMBERS, or '' when the
    questionnaire has no numbered section 6."""
    if not numbered:
        return ""
    numbers = [QUESTION_NUMBERS[field] for field in fields]
    if len(numbers) == 1:
        return f" (question {numbers[0]})"
    return f" (questions {', '.join(map(str, numbers[:-1]))} and {numbers[-1]})"


def flag_action(flag: str, client: str, numbered: bool = True) -> str:
    """The client-facing action for a flag, with its question numbers taken from the one questionnaire map."""
    text = QUESTION_REF.sub(lambda m: question_ref(m.group(1).split(","), numbered), FLAG_ACTIONS[flag])
    return text.format(client=client)


# Addendum §4.3: when Recommended next steps are shown, they replace these flag actions in "Flags to clear first".
INPUT_GAP_FLAGS = ("VALUE_AT_STAKE_MISSING", "VALUE_AT_STAKE_PARTIAL", "NO_SUCCESS_METRIC")
SIGNALS = (  # fixed Exhibit 1 order
    ("urgency", "Urgency"),
    ("budget_fit", "Budget fit"),
    ("tech_readiness", "Readiness"),
    ("strategic_value", "Strategic value"),
)
INPUTS_SENTENCES = {  # §3.5: on screen only, never in the PDF or Markdown
    "complete": "Every answer needed for a firm quote is in; confirm scope on a kickoff call.",
    "partial": "The value at stake is computed, but the flags below need clearing before a firm quote.",
    "thin": "Ask for weekly hours, an hourly cost and the missing answers before quoting.",
}


def default_report_date() -> str:
    return _date.today().strftime("%B %-d, %Y")


def _money(value: int) -> str:
    return f"${value:,}"


def value_at_stake_text(score: OpportunityScore) -> str | None:
    """The value at stake as shown everywhere: "about $X", or "at least $X" when it is a lower bound."""
    if score.value_at_stake is None:
        return None
    return f"{'at least' if score.value_at_stake_partial else 'about'} {_money(score.value_at_stake)}"


TITLE_MAX_WORDS = 15  # §8.7 action titles; the client name counts as one token (Proto, on #39)


def title_word_count(title: str, client: str = "") -> int:
    """Words in an action title, with the client name counted as ONE token however long it is."""
    name = client.strip()
    if name and name in title:
        return len(title.replace(name, "", 1).split()) + 1
    return len(title.split())


def _fit_title(full: str, short: str, client: str) -> str:
    """The named title when it fits the limit (name = one token); otherwise the template's short form without it."""
    return full if title_word_count(full, client) <= TITLE_MAX_WORDS else short


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _task_phrase(task: str) -> str:
    """'Lead triage' -> 'lead triage', but keep 'CRM updates' and 'HubSpot sync' as written."""
    if len(task) > 1 and task[0].isupper() and not task[1].isupper() and not re.match(r"\w+[A-Z]", task):
        return task[0].lower() + task[1:]
    return task


def _hours(value: float) -> str:
    return f"{value:g}"


def missing_answers(issues: list[ValidationIssue], flags: list[str]) -> list[tuple[str, str]]:
    """(short label, sentence) for every missing answer: validation issues plus answer flags, de-duplicated."""
    codes = {issue.code for issue in issues}
    items = [(ISSUE_LABELS.get(issue.code, issue.code.replace("missing_", "").replace("_", " ")), issue.message)
             for issue in issues]
    for flag in flags:
        if flag in ANSWER_FLAGS:
            label, duplicates, sentence = ANSWER_FLAGS[flag]
            if not codes.intersection(duplicates):
                items.append((label, sentence))
    return items


def inputs_confidence(score: OpportunityScore, issues: list[ValidationIssue]) -> str:
    """§3.5: thin = value missing or 3+ required answers missing; partial = any flag or gap (a partial value at
    stake, the "at least $X" lower bound, is partial per the approved spec); complete otherwise."""
    if score.value_at_stake is None or len(issues) >= 3:
        return "thin"
    if score.flags or issues:
        return "partial"
    return "complete"


def sources(client: str, date: str) -> list[str]:
    return [f"Data: {client} discovery questionnaire, received {date}.", *WORKS]


def _pending(intake: ClientIntake, score: OpportunityScore) -> list[str]:
    pending = []
    if not intake.budget.strip():
        pending.append("a budget range")
    if score.value_at_stake is None:
        pending += ["weekly hours", "an hourly cost"]
    elif score.value_at_stake_partial:
        pending.append("weekly hours for every listed task")
    return pending


def recommendation_sentence(client: str, intake: ClientIntake, score: OpportunityScore) -> str:
    pending = _pending(intake, score)
    if pending:
        return f"{client} fits a {score.tier}, pending {_join(pending)}."
    return f"{client} should start with a {score.tier} ({score.timeline})."


def _signal_titles(score: OpportunityScore) -> str:
    values = [(key, label, getattr(score, key)) for key, label in SIGNALS
              if not (key == "strategic_value" and score.strategic_value_default)]
    low = min(values, key=lambda item: item[2])
    high = max(values, key=lambda item: item[2])
    if low[2] == high[2]:
        return f"All {len(values)} scored signals are level at {low[2]} of 5"
    return f"{low[1]} is the weakest signal at {low[2]} of 5; {high[1].lower()} leads at {high[2]}"


def _as_list(items) -> list[str]:
    if items is None:
        return []
    if isinstance(items, (list, tuple, set)):
        return [str(item) for item in items]
    return [str(items)]


def build_report(intake: ClientIntake, score: OpportunityScore, issues: list[ValidationIssue],
                 report_date: str | None = None) -> dict:
    """The answer-first report as data: cover, ordered sections (label, action title, Markdown body), exhibit."""
    intake = replace(intake, pain_points=_as_list(intake.pain_points), goals=_as_list(intake.goals))
    date = report_date or default_report_date()
    client = intake.company_name.strip() or "The client"
    flags = list(score.flags)
    missing = missing_answers(issues, flags)
    steps = next_steps(client, intake, score)  # addendum §4: None when no key input is missing
    actions = [flag_action(flag, client, intake.workflows_section) for flag in flags
               if flag in FLAG_ACTIONS and not (steps and flag in INPUT_GAP_FLAGS)]
    top = score.workflows[0] if score.workflows else None
    pending = _pending(intake, score)
    reasons = score.signal_reasons or {}

    # 01 Summary (§3.2)
    lines = [recommendation_sentence(client, intake, score)]
    if not pending and top and top.get("value") is not None:
        lines.append(f"Today {client} runs {_task_phrase(top['task'])} by hand: about {_hours(top['hours'])} hours "
                     f"a week, roughly {_money(top['value'])} a year.")
    n_pain = len(intake.pain_points)
    if n_pain:
        first = intake.pain_points[0].strip().rstrip(".")
        lines.append(f"{_plural(n_pain, 'pain point')} slow the team, led by \"{first}\"."
                     if n_pain > 1 else f"1 pain point slows the team: \"{first}\".")
    else:
        lines.append("The questionnaire lists no pain points.")
    if score.strategic_value_default:
        lines.append("Strategic value is set to 3 of 5 until weekly hours and an hourly cost are provided.")
    elif score.value_at_stake_partial:
        lines.append(f"The value at stake is at least {_money(score.value_at_stake)} a year; tasks without weekly "
                     "hours are excluded, so the figure is a lower bound.")
    why = ["Why this tier:", ""] + [
        f"- {label} {getattr(score, key)}/5: {reasons.get(key, '')}." for key, label in SIGNALS[:3]
    ]
    if steps:
        next_step = next_step_from_gap(client, steps["gaps"])
    else:
        next_step = actions[0] if actions else "Confirm scope on a kickoff call."
    summary = "\n\n".join(lines) + "\n\n" + "\n".join(why) + f"\n\nNext step: {next_step}"

    # 02 What is missing
    labels = [label for label, _ in missing]
    if not missing:
        missing_title = "0 missing answers block a firm quote"
    else:
        named = labels if len(labels) <= 2 else labels[:2] + [f"{len(labels) - 2} more"]
        verb = "blocks" if len(missing) == 1 else "block"
        missing_title = f"{_plural(len(missing), 'missing answer')} {verb} a firm quote: {_join(named)}"
    missing_body = ("\n".join(f"{i}. {sentence}" for i, (_, sentence) in enumerate(missing, 1))
                    if missing else "The questionnaire answers every required question.")
    if steps:  # path 1 first, then the single path 2 sentence; before the flags (§4.1)
        missing_body += "\n\n" + next_steps_markdown(steps)
    if actions:
        missing_body += "\n\n**Flags to clear first**\n\n" + "\n".join(f"- {action}" for action in actions)

    # 03 Recommendation
    rec_title = _fit_title(f"{client} fits a {score.tier}, delivered over {score.timeline}",
                           f"A {score.tier} fits, delivered over {score.timeline}", client)
    rec_lines = [
        f"- **Tier:** {score.tier}",
        f"- **Scope:** {score.scope}",
        f"- **Pricing:** {CLIENT_PRICING_TEXT}",
        f"- **Timeline:** {score.timeline}",
        f"- **Next step:** {next_step}",
    ]
    rec_body = "\n".join(rec_lines)
    if score.scope_notes:
        rec_body += "\n\n**Scope additions**\n\n" + "\n".join(f"- {note}" for note in score.scope_notes)
    if score.quick_wins:
        rec_body += "\n\n**Quick win**\n\n" + "\n".join(f"- {win}" for win in score.quick_wins)

    # 04 Why this tier (Exhibit 1, §3.4)
    why_title = _signal_titles(score)
    bars = []
    for key, label in SIGNALS:
        value = getattr(score, key)
        default = key == "strategic_value" and score.strategic_value_default
        bars.append({"key": key, "label": label, "value": value, "default": default,
                     "text": f"{value}/5 default" if default else f"{value}/5"})
    # At most one bar is highlighted (the weakest), and it is also labelled in text, so colour never works alone.
    highlight = next((b["key"] for b in bars if not b["default"] and why_title.startswith(f"{b['label']} is the weakest")),
                     None)
    for bar in bars:
        bar["highlight"] = bar["key"] == highlight
    exhibit = {
        "label": "Exhibit 1",
        "title": why_title,
        "unit": "Sub-scores, points out of 5",
        "bars": bars,
        "total": f"Score {score.total_score} of {score.max_score}",
        "source": f"Source: {client} discovery questionnaire, {date}; DIA scoring rules v{score.rules_version}",
    }
    exhibit_md = "\n".join([
        f"| {exhibit['unit']} | Points |", "|---|---|",
        *[f"| {bar['label']}{' (weakest)' if bar['highlight'] else ''} | {bar['text']} |" for bar in bars],
    ]) + f"\n\n{exhibit['total']}\n\n{exhibit['source']}"
    # Each line cites the evidence that sets the sub-score (Proto, on #39), not the summary's "Why this tier" bullets.
    evidence = score.signal_evidence or {}
    if evidence:
        why_lines = score.reasons[:1] + [evidence[key] for key, _ in SIGNALS if evidence.get(key)]
    else:  # a score object built before the evidence field existed
        why_lines = list(score.reasons)
    why_body = "\n".join(f"- {line}" for line in why_lines)

    # 05 Top workflows by value at stake (§2.4)
    if score.value_at_stake is not None:
        n = len([w for w in score.workflows if w.get("value") is not None])
        if score.value_at_stake_partial:
            tail = f"{value_at_stake_text(score)} a year to {n} of {_plural(len(score.workflows), 'manual workflow')}"
        else:
            tail = f"{value_at_stake_text(score)} a year to {_plural(n, 'manual workflow')}"
        wf_title = _fit_title(f"{client} loses {tail}", f"Manual work loses {tail}", client)
    else:
        wf_title = "Strategic value stays at 3 of 5 until hours and an hourly cost are provided"
    if score.workflows:
        rows = ["| Task | Hours a week | Value a year | Flags |", "|---|---|---|---|"]
        for w in score.workflows:
            rows.append(f"| {w['task']} | {_hours(w['hours']) if w.get('hours') is not None else 'Not provided'} | "
                        f"{_money(w['value']) if w.get('value') is not None else 'Not provided'} | "
                        f"{'; '.join(w.get('gaps') or []) or 'None'} |")
        wf_body = "\n".join(rows)
    else:
        wf_body = "The questionnaire lists no manual tasks with weekly hours."

    # 06 Client profile (plain noun heading)
    profile = [
        "### Overview",
        f"- **Industry:** {intake.industry or 'TBD'}",
        f"- **Size:** {intake.size or 'TBD'}",
        f"- **Location:** {intake.location or 'TBD'}",
        f"- **Website:** {intake.website or 'TBD'}",
        f"- **Years in business:** {intake.years_in_business or 'TBD'}",
        "", "### Current tools", intake.tools or "None listed.",
        "", "### Decision context",
        f"- **Decision maker:** {intake.decision_maker or 'TBD'}",
        f"- **Preferred start:** {intake.start_date or 'TBD'}",
        f"- **Regulated data:** {intake.sensitive_data or intake.compliance or 'None listed'}",
    ]
    if intake.workflows_section:
        profile += ["", "### Operations"] + [
            f"- **{label}:** {value or 'Not provided'}" for label, value in (
                ("Process owner", intake.process_owner),
                ("Systems and access", intake.systems_access),
                ("Data and readiness", intake.data_readiness),
                ("Exceptions", intake.exceptions),
                ("Previous attempts", intake.past_attempts),
                ("Maintenance and support", intake.maintenance),
                ("Finance", intake.finance_signals),
                ("Success metric", intake.success_metric),
            )]
    profile += ["", "### Notes", intake.notes or "None provided."]

    # 07 Pain points and goals
    def numbered(items: list[str]) -> str:
        return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)) if items else "None listed."
    pains = f"### Pain points\n{numbered(intake.pain_points)}\n\n### Goals\n{numbered(intake.goals)}"

    # 08 Delivery plan and investment
    delivery = "\n".join([
        "### Delivery plan",
        "- **Phase 1:** Confirm the intake fields, the missing answers and the success metric.",
        "- **Phase 2:** Configure the agent workflow and the document templates.",
        "- **Phase 3:** Run sample intakes, review the outputs and hand off the operating notes.",
        *[f"- **Scope addition:** {note}" for note in score.scope_notes],
        "", "### Investment",
        f"- **Recommended solution:** {score.tier}. {score.scope}",
        f"- **Pricing:** {CLIENT_PRICING_TEXT}",
        f"- **Timeline:** {score.timeline}",
        f"- **Next step:** {next_step}",
    ])

    method_text = METHOD_AND_SOURCES
    if score.value_at_stake_partial:
        method_text = method_text.replace("text appears in it.\n", f"text appears in it.\n{METHOD_PARTIAL_NOTE}\n", 1)
    method = method_text.replace("× 48 weeks", f"× {_weeks_label()} weeks", 1).format(client=client, date=date)

    bodies = {
        "summary": (None, summary),
        "missing": (missing_title, missing_body),
        "recommendation": (rec_title, rec_body),
        "why": (why_title, why_body),
        "workflows": (wf_title, wf_body),
        "profile": (None, "\n".join(profile)),
        "pains": (None, pains),
        "delivery": (None, delivery),
        "method": (None, method),
    }
    sections = []
    for number, (key, label, doc) in enumerate(SECTIONS, 1):
        title, body = bodies[key]
        sections.append({"no": f"{number:02d}", "key": key, "label": label, "title": title, "body": body, "doc": doc})

    return {
        "client": client,
        "date": date,
        "cover_sentence": recommendation_sentence(client, intake, score),
        "sections": sections,
        "exhibit": exhibit,
        "exhibit_md": exhibit_md,
        "missing": [sentence for _, sentence in missing],
        "missing_labels": labels,
        "actions": actions,
        "next_steps": steps,
        "input_gaps": steps["gaps"] if steps else [],
        "action_titles": [s["title"] for s in sections if s["title"]],
        "inputs_confidence": inputs_confidence(score, issues),
        "sources": sources(client, date),
        "method_and_sources": method,
    }


def section_markdown(section: dict, exhibit_md: str = "") -> str:
    parts = [f"## {section['label']}"]
    if section["key"] == "why":
        parts.append("Exhibit 1")
    if section["title"]:
        parts.append(f"### {section['title']}")
    if section["key"] == "why" and exhibit_md:
        parts.append(exhibit_md)
    if section["key"] == "method":
        # Hard line breaks keep the §5 block's lines when Markdown is rendered.
        parts.append("\n".join(line + "  " if line.strip() else line for line in section["body"].split("\n")))
    else:
        parts.append(section["body"])
    return "\n\n".join(parts)


def render_documents(report: dict) -> dict[str, str]:
    docs: dict[str, list[str]] = {name: [f"# {DOC_TITLES[name]}: {report['client']}"] for name in DOC_TITLES}
    for section in report["sections"]:
        docs[section["doc"]].append(section_markdown(section, report["exhibit_md"]))
    return {name: "\n\n".join(parts) + "\n" for name, parts in docs.items()}
