"""Better inputs (dia-savant inputs addendum v1.1.1): the five key inputs, the client-facing "Recommended next
steps" and the path 2 consulting sentence. Deterministic and template-only: nothing here estimates or fills a value.

Config (env-overridable; never hard-coded in templates):
- DIA_CONSULTING_INTAKE_URL: the services page linked after the path 2 sentence. Defaults to Ikenna's card page;
  set it to an empty value to drop the "Its services are listed at ..." sentence and the on-screen link.
- DIA_PATH2_TRIGGER: "broad" (default, decision 4: any input gap) or "narrow" (only hours, hourly cost or
  success metric gaps).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from .models import ClientIntake, OpportunityScore

CONSULTING_NAME = "Bash Systems & Services"  # decision 2
DEFAULT_CONSULTING_INTAKE_URL = (
    "https://script.google.com/macros/s/AKfycbwg6ZbVfSHS0-ApjUwGxPZkLOzrHJnPtWfqv4Kylj2Vq20z4rra70MfnhUVaSTuBW4P8w"
    "/exec?view=card"
)
PATH2_TRIGGERS = ("broad", "narrow")
VALUE_GAPS = ("hours", "hourly_cost", "success_metric")

# §2: fixed display order. key -> (label, client question for the PDF/MD, on-screen thinking prompt, why line)
INPUTS = {
    "hours": ("Hours per task",
              "Which three tasks take the most hours each week, and how many hours does each take?",
              "Which three tasks take the most hours each week?",
              "Hours turn the value at stake into a figure."),
    "hourly_cost": ("Hourly cost",
                    "What does one hour of the people doing these tasks cost the business, including benefits?",
                    "What does an hour of that person's time cost the business, all-in?",
                    "Hours times cost gives the yearly value at stake."),
    "success_metric": ("Success metric",
                       "Which single number would show that the work succeeded, and what is its value today?",
                       "What one number would show this worked, and what is it today?",
                       "A number agreed now makes the result easy to check later."),
    "people": ("People involved",
               "Who carries out each task, and how many people share it?",
               "Who does each of those tasks, and how many people share them?",
               "It shows who the change affects."),
    "systems": ("Systems",
                "Which systems does each task use, and who can grant access to them?",
                "Which systems does each task touch, and who can grant access?",
                "Access is where scope surprises usually start."),
}
HOURS_PARTIAL_QUESTION = "Which of the listed tasks have no weekly hours yet, and how many hours does each take?"
NEXT_STEPS_TITLE = "Recommended next steps"


def consulting_url() -> str | None:
    """DIA_CONSULTING_INTAKE_URL (https only), defaulting to Ikenna's card page. Empty or invalid = unset."""
    raw = os.environ.get("DIA_CONSULTING_INTAKE_URL", DEFAULT_CONSULTING_INTAKE_URL).strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or any(ch in raw for ch in "\"'<> \t\r\n"):
        return None
    return raw


def path2_trigger() -> str:
    value = os.environ.get("DIA_PATH2_TRIGGER", "broad").strip().lower()
    return value if value in PATH2_TRIGGERS else "broad"


def _blank(text: str) -> bool:
    return (text or "").strip().lower() in {"", "n/a", "none", "tbd", "your answer", "-"}


def input_gaps(intake: ClientIntake, score: OpportunityScore) -> list[str]:
    """§2: the missing key inputs, in fixed order. No effect on the score. Old questionnaires (no section 6) only
    get hours, hourly cost and success metric."""
    from .core import parse_hourly_rate, parse_workflows

    tasks = parse_workflows(intake.workflow_tasks)
    flags = set(score.flags)
    gaps = []
    if not tasks or any(task["hours"] is None for task in tasks):
        gaps.append("hours")
    if parse_hourly_rate(intake.hourly_cost) is None:
        gaps.append("hourly_cost")
    if "NO_SUCCESS_METRIC" in flags:
        gaps.append("success_metric")
    if intake.workflows_section:
        if tasks and any(task["people"] is None for task in tasks):
            gaps.append("people")
        if _blank(intake.systems_access) and "SCOPE_RISK_SYSTEMS" not in flags:
            gaps.append("systems")
    return gaps


def _mid(client: str) -> str:
    return "the client" if client == "The client" else client


def path2_sentences(client: str, gaps: list[str]) -> list[str]:
    """Path 2: one sentence, plus the services sentence when the URL is set. Empty when path 2 does not apply."""
    if not gaps:
        return []
    if path2_trigger() == "narrow" and not set(gaps) & set(VALUE_GAPS):
        return []
    sentences = [f"If {_mid(client)} would like help setting up this tracking or measurement, "
                 f"{CONSULTING_NAME} offers business consulting."]
    url = consulting_url()
    if url:
        sentences.append(f"Its services are listed at {url}.")
    return sentences


def next_steps(client: str, intake: ClientIntake, score: OpportunityScore) -> dict | None:
    """§4.3: the Recommended next steps block (path 1 intro, one item per gap, then path 2 once), or None."""
    gaps = input_gaps(intake, score)
    if not gaps:
        return None
    items = []
    for key in gaps:
        label, question, _, _ = INPUTS[key]
        if key == "hours" and score.value_at_stake_partial:
            question = HOURS_PARTIAL_QUESTION
        items.append({"key": key, "label": label, "question": question})
    path2 = path2_sentences(client, gaps)
    return {
        "title": NEXT_STEPS_TITLE,
        "intro": f"{client} can work these out with its own team and tools, then return the updated answers "
                 "for a new DIA run.",
        "items": items[:5],
        "path2": path2[0] if path2 else "",
        "services": path2[1] if len(path2) > 1 else "",
        "services_url": consulting_url() if len(path2) > 1 else None,
        "gaps": gaps,
    }


def next_step_from_gap(client: str, gaps: list[str]) -> str:
    return (f"{client} works out the {INPUTS[gaps[0]][0].lower()} and returns the answers for a new DIA run "
            "(see What is missing).")


def next_steps_markdown(block: dict) -> str:
    lines = [f"**{block['title']}**", "", block["intro"], ""]
    lines += [f"{i}. **{item['label']}.** {item['question']}" for i, item in enumerate(block["items"], 1)]
    tail = " ".join(s for s in (block["path2"], block["services"]) if s)
    if tail:
        lines += ["", tail]
    return "\n".join(lines)
