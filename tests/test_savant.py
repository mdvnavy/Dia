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


# --- §8.3 Scorer v2 ----------------------------------------------------------------------------

from client_discovery.core import score_opportunity, strategic_value_for, value_bands, value_weeks  # noqa: E402


def score_of(text: str):
    return score_opportunity(parse_questionnaire_markdown(text))


ONE_TASK = "Invoice entry - office manager, {h} hours a week, 1 person"


def test_value_at_stake_is_hours_times_rate_times_48():  # §8
    s = score_of(questionnaire(q1="Data entry - clerk, 6 hours a week, 1 person; Scheduling - founder, 4.5 hours a week, 1 person",
                               q2="$50 an hour"))
    assert s.value_at_stake == round((6 + 4.5) * 50 * 48) == 25200
    assert [w["value"] for w in s.workflows] == [6 * 50 * 48, round(4.5 * 50 * 48)]


@pytest.mark.parametrize("hours, rate, value, strategic", [
    (1, "104.15", 4999, 2), (1, "104.1667", 5000, 3), (4, "104.1667", 20000, 4), (10, "104.1667", 50000, 5),
])
def test_value_bands_map_4999_5000_20000_50000_to_2_3_4_5(hours, rate, value, strategic):  # §8
    s = score_of(questionnaire(q1=ONE_TASK.format(h=hours), q2=f"${rate} an hour"))
    assert s.value_at_stake == value
    assert s.strategic_value == strategic
    assert "VALUE_AT_STAKE_MISSING" not in s.flags


def test_bands_and_weeks_are_configurable(monkeypatch):
    assert value_weeks() == 48 and value_bands() == (5000, 20000, 50000)
    monkeypatch.setenv("DIA_VALUE_WEEKS", "50")
    monkeypatch.setenv("DIA_VALUE_BANDS", "1000,2000,3000")
    s = score_of(questionnaire(q1=ONE_TASK.format(h=1), q2="$50 an hour"))
    assert s.value_at_stake == 2500 and s.strategic_value == 4
    assert strategic_value_for(999) == 2


def test_salary_band_becomes_an_hourly_rate():
    s = score_of(questionnaire(q1=ONE_TASK.format(h=10), q2="$52,000-$62,000 a year"))
    assert s.value_at_stake == round(10 * (57000 / 2080) * 48)


@pytest.mark.parametrize("missing", [{"q1": ""}, {"q2": ""}, {"q1": "Invoice entry - office manager"}, {"q2": "not sure"}],
                         ids=["no-q1", "no-q2", "q1-without-hours", "q2-without-a-number"])
def test_missing_value_at_stake_keeps_strategic_value_3_with_a_flag(missing):  # §8 (summary line: see §8.4 tests)
    s = score_of(questionnaire(**missing))
    assert s.strategic_value == 3 and s.strategic_value_default
    assert "VALUE_AT_STAKE_MISSING" in s.flags
    assert s.value_at_stake is None


def test_legacy_questionnaire_scores_as_before_with_two_flags():
    s = score_of(LEGACY)
    assert (s.total_score, s.urgency, s.budget_fit, s.tech_readiness, s.strategic_value) == (12, 2, 3, 4, 3)
    assert s.flags == ["VALUE_AT_STAKE_MISSING", "NO_SUCCESS_METRIC"]


@pytest.mark.parametrize("q3", ["", "Faster responses"], ids=["empty", "no-number"])
def test_no_success_metric_is_a_flag_only(q3):  # §8
    filled, empty = score_of(SAMPLE), score_of(questionnaire(q3=q3))
    assert "NO_SUCCESS_METRIC" in empty.flags and "NO_SUCCESS_METRIC" not in filled.flags
    assert (empty.tier, empty.price_range, empty.timeline, empty.scope) == (filled.tier, filled.price_range, filled.timeline, filled.scope)
    assert empty.total_score == filled.total_score  # no cap, no score change


def test_sample_scores():
    s = score_of(SAMPLE)
    assert (s.tier, s.urgency, s.budget_fit, s.tech_readiness, s.strategic_value, s.total_score) == ("Custom AI Agent", 3, 4, 4, 4, 15)
    assert s.value_at_stake == 36960 and s.flags == ["TRUST_REPAIR"]


@pytest.mark.parametrize("answers, flag, effect", [
    ({"q4": ""}, "NO_OWNER", "readiness-1"),
    ({"q6": "Mostly in HubSpot; about 2 of 5"}, "DATA_CLEANUP_FIRST", "readiness-1"),
    ({"q6": "Spreadsheets and email threads"}, "DATA_CLEANUP_FIRST", "readiness-1"),
    ({"q5": "HubSpot; no API access"}, "SCOPE_RISK_SYSTEMS", "timeline"),
    ({"q8": "Yes, client health records"}, "COMPLIANCE_REVIEW", None),
    ({"q9": "None"}, None, "no-trust-repair"),
    ({"q7": "Duplicates; VIP requests; refunds; missing PO numbers"}, "HUMAN_IN_LOOP", "scope"),
    ({"q10": ""}, "SUPPORT_TERMS_NEEDED", None),
])
def test_section_6_flags_and_adjustments(answers, flag, effect):
    base, s = score_of(SAMPLE), score_of(questionnaire(**answers))
    if flag:
        assert flag in s.flags
    if effect == "readiness-1":
        assert s.tech_readiness == base.tech_readiness - 1
    if effect == "timeline":
        assert (base.timeline, s.timeline) == ("2-4 weeks", "2-6 weeks")
    if effect == "no-trust-repair":
        assert "TRUST_REPAIR" not in s.flags
    if effect == "scope":
        assert any(note.startswith("Hand-off rules") for note in s.scope_notes)
    assert s.tier == base.tier  # flags never change the tier


def test_time_sink_adds_one_urgency_capped_at_5():
    plain = score_of(questionnaire(q1="Lead triage - sales rep, 6 hours a week, 1 person"))
    sink = score_of(questionnaire(q1="Bookkeeping - founder, 6 hours a week, 1 person"))
    assert sink.urgency == plain.urgency + 1


def test_budget_against_value():
    covered = score_of(questionnaire(q1=ONE_TASK.format(h=10), q2="$50 an hour"))  # $24,000 > $10,000 top
    weak = score_of(questionnaire(q1=ONE_TASK.format(h=1), q2="$50 an hour"))      # $2,400; ratio 4.2
    base = score_of(questionnaire(q1=ONE_TASK.format(h=5), q2="$30 an hour"))      # $7,200; ratio 1.4
    assert covered.budget_fit == base.budget_fit + 1 and "ROI_WEAK" not in covered.flags
    assert "ROI_WEAK" in weak.flags and weak.budget_fit == base.budget_fit


def test_overdue_invoices_suggest_an_accounts_receivable_quick_win():
    assert score_of(questionnaire(q11="35% of invoices; 4 hours a week")).quick_wins
    assert not score_of(SAMPLE).quick_wins


@pytest.mark.parametrize("text", [SAMPLE, LEGACY, questionnaire(q1="", q2="", q3="", q4="", q6="1 of 5")])
def test_max_score_is_still_20(text):  # §8 (cabinet chip: see §8.6 tests)
    s = score_of(text)
    assert s.max_score == 20 and 0 < s.total_score <= 20
    assert all(1 <= v <= 5 for v in (s.urgency, s.budget_fit, s.tech_readiness, s.strategic_value))
