"""dia-savant v1.0 (proto/dia-savant-proposal.md): questionnaire section 6, scorer v2, answer-first documents
and PDF, and the on-screen Inputs label. One test (or test group) per §8 acceptance test is marked "§8"."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from client_discovery.core import parse_questionnaire_markdown, validate_intake

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / "samples/sample_questionnaire.md").read_text(encoding="utf-8")
LEGACY = (ROOT / "tests/fixtures/complete_questionnaire.md").read_text(encoding="utf-8")

Q = {  # section 6 question prefixes, as in the sample
    1: "Top 1-3 manual tasks", 2: "Loaded hourly cost", 3: "The one number", 4: "Who owns the process",
    5: "Systems involved", 6: "Where does the data live", 7: "What goes wrong today", 8: "Does this touch regulated",
    9: "Previous AI or automation", 10: "After launch, who maintains", 11: "Finance only",
}


def questionnaire(base: str = SAMPLE, **answers: str) -> str:
    """The sample with section 6 answers replaced: questionnaire(q1="...", q3="")."""
    text = base
    for key, answer in answers.items():
        prefix = Q[int(key[1:])]
        text, n = re.subn(rf"^(\| {re.escape(prefix)}[^|]*\|)[^\n]*\|$", lambda m: f"{m.group(1)} {answer} |", text, flags=re.M)
        assert n == 1, key
    return text


# --- §8.2 Questionnaire section 6 and parser aliases ---------------------------------------------

def test_sample_has_section_6_with_eleven_questions_in_the_table_format():
    section = SAMPLE[SAMPLE.index("## 6. Workflows and value"):]
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith(("| Question", "| ---"))]
    assert len(rows) == 11
    for n, prefix in Q.items():
        assert rows[n - 1].startswith(f"| {prefix}"), n
    assert "| Compliance requirements |" not in SAMPLE  # Q8 replaces the free-text compliance line


def test_parser_reads_every_section_6_answer():
    intake = parse_questionnaire_markdown(SAMPLE)
    assert intake.workflows_section
    assert intake.workflow_tasks.startswith("Lead triage across three forms")
    assert intake.hourly_cost == "$55 an hour"
    assert intake.success_metric.startswith("Lead response time")
    assert intake.process_owner.startswith("The sales coordinator owns intake")
    assert "API access" in intake.systems_access
    assert intake.data_readiness == "Mostly in HubSpot; about 3 of 5"
    assert intake.exceptions.startswith("Duplicate leads")
    assert intake.sensitive_data == "No"
    assert "abandoned" in intake.past_attempts
    assert "monthly check-in" in intake.maintenance
    assert intake.finance_signals.startswith("18%")
    # Sections 1-5 parse as before.
    assert intake.company_name == "Northstar Studio" and intake.budget == "$2,500-$10,000"
    assert len(intake.pain_points) == 2 and len(intake.goals) == 2


def test_complete_sample_has_no_issues():
    assert validate_intake(parse_questionnaire_markdown(SAMPLE)) == []


@pytest.mark.parametrize("q, code", [("q1", "missing_workflow_tasks"), ("q2", "missing_hourly_cost"), ("q3", "missing_success_metric")])
def test_q1_to_q3_are_required(q, code):
    issues = validate_intake(parse_questionnaire_markdown(questionnaire(**{q: ""})))
    assert [issue.code for issue in issues] == [code]


def test_q4_to_q11_are_optional():
    blank = {f"q{n}": "" for n in range(4, 12)}
    assert validate_intake(parse_questionnaire_markdown(questionnaire(**blank))) == []


def test_questionnaires_without_section_6_keep_their_issue_list():
    intake = parse_questionnaire_markdown(LEGACY)
    assert not intake.workflows_section
    assert validate_intake(intake) == []
