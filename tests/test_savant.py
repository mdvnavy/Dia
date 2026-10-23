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


def _urgency_reason(**answers: str) -> str:
    return score_of(questionnaire(**answers)).signal_reasons["urgency"]


def test_time_sink_named_is_the_highest_value_matching_task():
    # Scheduling (coordination) is worth more than bookkeeping (finance admin), so keyword order no longer wins.
    reason = _urgency_reason(q1="Bookkeeping - founder, 2 hours a week, 1 person; "
                                "Scheduling - office manager, 8 hours a week, 1 person", q2="$50 an hour")
    assert reason.endswith("matches a known time sink (coordination)")
    # Order in Q1 does not matter either.
    reason = _urgency_reason(q1="Scheduling - office manager, 1 hour a week, 1 person; "
                                "Bookkeeping - founder, 9 hours a week, 1 person", q2="$50 an hour")
    assert reason.endswith("(finance admin)")


def test_time_sink_ignores_the_person_named_after_the_task():
    # "sales coordinator" is who does it, not coordination work; lead intake is not a scored category (§2.1).
    reason = _urgency_reason(q1="Lead triage - sales coordinator, 9 hours a week, 1 person; "
                                "Invoice chasing - founder, 2 hours a week, 1 person", q2="$50 an hour")
    assert reason.endswith("(finance admin)")


def test_sample_urgency_names_its_highest_value_known_time_sink():
    # Lead triage ($15,840) and CRM updates ($13,200) match no category; invoice chasing ($7,920) is finance admin.
    assert score_of(SAMPLE).signal_reasons["urgency"] == (
        "2 pain points listed, and the manual work matches a known time sink (finance admin)")


def test_time_sink_tie_falls_back_to_keyword_order():
    for q1 in ("Scheduling - office manager, 4 hours a week, 1 person; Bookkeeping - founder, 4 hours a week, 1 person",
               "Bookkeeping - founder, 4 hours a week, 1 person; Scheduling - office manager, 4 hours a week, 1 person"):
        assert _urgency_reason(q1=q1, q2="$50 an hour").endswith("(finance admin)")
    # One task in two categories is named by the first in keyword order.
    assert _urgency_reason(q1="Invoice chasing - founder, 4 hours a week, 1 person",
                           q2="$50 an hour").endswith("(finance admin)")


@pytest.mark.parametrize("answers", [
    {"q1": "Scheduling - office manager, 8 hours a week, 1 person; Bookkeeping - founder, 2 hours a week, 1 person",
     "q2": ""},                                                                       # no hourly cost
    {"q1": "Scheduling - office manager; Bookkeeping - founder", "q2": "$50 an hour"},  # no hours
])
def test_time_sink_without_values_falls_back_to_keyword_order(answers):
    s = score_of(questionnaire(**answers))
    assert all(w["value"] is None for w in s.workflows)
    assert s.signal_reasons["urgency"].endswith("(finance admin)")


def test_time_sink_unvalued_match_falls_back_even_when_other_tasks_have_values():
    # The only valued task matches no category, so the keyword-order rule names the sink.
    reason = _urgency_reason(q1="Lead triage - sales rep, 8 hours a week, 1 person; Scheduling - founder; "
                                "Bookkeeping - founder", q2="$50 an hour")
    assert reason.endswith("(finance admin)")


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


# --- §8.4 Answer-first documents (Markdown). The PDF side of each check is in the §8.5 block. ---------

import re  # noqa: E402

from client_discovery import report_model  # noqa: E402
from client_discovery.core import generate_documents, scrub_for_export, validate_intake  # noqa: E402

DATE = "October 23, 2026"
SECTION_LABELS = [label for _, label, _ in report_model.SECTIONS]
MIN_ORDER = ["Summary", "What is missing", "Recommendation", "Why this tier", "Top workflows by value at stake",
             "Client profile", "Pain points and goals", "Delivery plan and investment", "Method and sources"]
# Text the client wrote is quoted into the report as-is; template checks run on the report minus these.
CLIENT_FIELDS = ("company_name", "industry", "size", "location", "website", "years_in_business", "tools",
                 "decision_maker", "start_date", "budget", "notes", "compliance", "workflow_tasks", "hourly_cost",
                 "success_metric", "process_owner", "systems_access", "data_readiness", "exceptions",
                 "sensitive_data", "past_attempts", "maintenance", "finance_signals")
VARIANTS = {
    "sample": SAMPLE,
    "clean": questionnaire(q9="None"),
    "legacy": LEGACY,
    "no-q1": questionnaire(q1=""),
    "no-q3": questionnaire(q3=""),
    "flags": questionnaire(q4="", q5="HubSpot; no API access", q6="Spreadsheets", q8="Yes, health records",
                           q7="Duplicates; VIP requests; refunds", q10="", q11="40% of invoices"),
    "thin": questionnaire(q1="", q2="", q3="").replace("| Budget range | $2,500-$10,000 |", "| Budget range |  |"),
}


def report_for(text: str) -> tuple[dict, object, object]:
    intake = parse_questionnaire_markdown(text)
    score = score_opportunity(intake)
    return report_model.build_report(intake, score, validate_intake(intake), DATE), intake, score


def markdown_export(text: str) -> str:
    """What the Markdown download holds: the scrubbed documents in the export order."""
    intake = parse_questionnaire_markdown(text)
    docs = scrub_for_export(generate_documents(intake, score_opportunity(intake), report_date=DATE))
    return "\n\n---\n\n".join(docs[name] for name in report_model.MD_EXPORT_ORDER)


def template_text(text: str, rendered: str) -> str:
    """The rendered report with client-written answers and the verbatim §5 block taken out."""
    intake = parse_questionnaire_markdown(text)
    client = intake.company_name.strip() or "The client"
    out = rendered.replace(report_model.METHOD_AND_SOURCES.format(client=client, date=DATE), " ")
    hard_breaks = "\n".join(line + "  " if line.strip() else line
                             for line in report_model.METHOD_AND_SOURCES.format(client=client, date=DATE).split("\n"))
    out = out.replace(hard_breaks, " ")
    values = [getattr(intake, f) for f in CLIENT_FIELDS] + list(intake.pain_points) + list(intake.goals)
    values += [w["task"] for w in score_opportunity(intake).workflows]
    for value in sorted({v.strip() for v in values if v and len(v.strip()) > 3}, key=len, reverse=True):
        out = out.replace(value, " ")
        out = out.replace(value.rstrip("."), " ")
    return out


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_summary_follows_the_answer_first_template():  # §3.2, §9 item 1
    report, intake, score = report_for(SAMPLE)
    summary = report["sections"][0]["body"]
    lines = summary.split("\n\n")
    assert lines[0] == "Northstar Studio should start with a Custom AI Agent ($2,500-$10,000, 2-4 weeks)."
    assert lines[1] == "Today Northstar Studio runs lead triage across three forms by hand: about 6 hours a week, roughly $15,840 a year."
    assert lines[2].startswith('2 pain points slow the team, led by "Leads arrive from three forms')
    assert "Why this tier:\n\n- Urgency 3/5:" in summary and "\n- Budget fit 4/5:" in summary and "\n- Readiness 4/5:" in summary
    assert summary.endswith("Next step: Propose a paid pilot on a single workflow, measured against one agreed metric (question 9).")
    assert "needs a reliable way to turn discovery intake" not in markdown_export(SAMPLE)  # §9 item 1


@pytest.mark.parametrize("missing", ["q1", "q2"])
def test_missing_value_summary_has_the_default_line_and_the_pending_form(missing):  # §8.3
    report, _, score = report_for(questionnaire(**{missing: ""}))
    summary = report["sections"][0]["body"]
    assert score.strategic_value == 3 and "VALUE_AT_STAKE_MISSING" in score.flags
    assert "Strategic value is set to 3 of 5 until weekly hours and an hourly cost are provided." in summary
    assert summary.startswith("Northstar Studio fits a Custom AI Agent, pending weekly hours and an hourly cost.")
    assert "Today Northstar Studio runs" not in summary  # line 2 omitted
    assert "Strategic value is set to 3 of 5" in markdown_export(questionnaire(**{missing: ""}))


def test_kickoff_call_is_the_next_step_when_nothing_is_flagged():
    report, _, score = report_for(questionnaire(q9="None"))
    assert score.flags == [] and report["sections"][0]["body"].endswith("Next step: Confirm scope on a kickoff call.")
    assert report["inputs_confidence"] == "complete"


@pytest.mark.parametrize("name", VARIANTS)
def test_markdown_follows_the_pdf_section_order(name):  # §8.8 (Markdown)
    md = markdown_export(VARIANTS[name])
    headings = re.findall(r"(?m)^## (.+)$", md)
    assert headings == MIN_ORDER == SECTION_LABELS
    assert headings.index("Summary") < headings.index("What is missing") < headings.index("Client profile")


@pytest.mark.parametrize("name", VARIANTS)
def test_no_inputs_label_in_markdown(name):  # §8.2 (Markdown)
    md = markdown_export(VARIANTS[name])
    assert "Inputs:" not in md and "inputs_confidence" not in md


def test_titles_name_client_tier_price_timeline_weakest_and_missing_count():  # §8.6
    for text in VARIANTS.values():
        report, intake, score = report_for(text)
        titles = " ".join([report["cover_sentence"], *report["action_titles"]])
        client = intake.company_name.strip() or "The client"
        for needed in (client, score.tier, score.price_range, score.timeline):
            assert needed in titles
        values = {label: getattr(score, key) for key, label in report_model.SIGNALS
                  if not (key == "strategic_value" and score.strategic_value_default)}
        weakest = min(values.values())
        weakest_label = next(label for label, value in values.items() if value == weakest)
        assert f"{weakest_label} is the weakest signal at {weakest} of 5" in titles or "are level at" in titles
        n_missing = len(report["missing"])
        assert re.search(rf"\b{n_missing} missing answers?\b", titles)


def test_missing_answer_count_dedupes_flags_against_issues():
    report, _, _ = report_for(questionnaire(q1="", q3=""))
    labels = report["missing_labels"]
    assert labels.count("success metric") == 1 and "weekly hours and hourly cost" not in labels
    assert report["sections"][1]["title"] == "2 missing answers block a firm quote: manual tasks and success metric"
    legacy, _, _ = report_for(LEGACY)
    assert legacy["missing_labels"] == ["value inputs", "success metric"]
    labels = list(report_model.ISSUE_LABELS.values()) + [v[0] for v in report_model.ANSWER_FLAGS.values()]
    assert max(len(label.split()) for label in labels) <= 2  # keeps the title at 15 words or fewer


@pytest.mark.parametrize("name", VARIANTS)
def test_action_titles_have_at_most_15_words_a_digit_and_no_full_stop(name):  # §8.7
    report, _, _ = report_for(VARIANTS[name])
    titles = report["action_titles"] + [report["exhibit"]["title"]]
    assert len(report["action_titles"]) == 4
    for title in titles:
        assert len(title.split()) <= 15, title
        assert not title.endswith("."), title
        assert re.search(r"\d", title), title
        assert not re.search(r"\bwe\b", title, re.I), title
    md = markdown_export(VARIANTS[name])
    assert set(re.findall(r"(?m)^### (.+)$", md)) >= set(report["action_titles"])


PROPER = {"DIA", "AI", "Custom", "Agent", "Quick", "Win", "Full", "Integration", "Exhibit", "API"}


def is_sentence_case(heading: str, client: str) -> bool:
    words = heading.replace(client, "").split()
    if not words:
        return True
    first_ok = words[0][:1].isupper() or words[0][:1].isdigit() or heading.startswith(client)
    rest_ok = all(not w[:1].isupper() or w.strip(":;,()") in PROPER for w in words[1:])
    return first_ok and rest_ok


CONTRACTION = re.compile(r"n't\b|'s\s|'re\b|'ll\b|'ve\b|'d\b|'m\b", re.I)


@pytest.mark.parametrize("name", VARIANTS)
def test_markdown_headings_are_sentence_case_and_text_is_formal(name):  # §8.9 (Markdown)
    text = VARIANTS[name]
    report, intake, _ = report_for(text)
    md = markdown_export(text)
    client = report["client"]
    for heading in re.findall(r"(?m)^#{1,4} (.+)$", md):
        assert is_sentence_case(heading, client), heading
    body = template_text(text, md)
    assert not CONTRACTION.search(body), CONTRACTION.search(body)
    assert not re.search(r"\bwe\b|\bour\b|\bus\b", body, re.I)
    assert "!" not in body


def test_sentence_case_checker_catches_title_case():
    assert not is_sentence_case("Top Workflows By Value", "X")
    assert is_sentence_case("Budget fit is the weakest signal at 2 of 5; urgency leads at 4", "X")


RESEARCH_SOURCES = ("PwC", "Gartner", "RAND", "Deloitte", "RSM", "Asana", "QuickBooks", "Bain", "Hinge", "CFIB", "MIT",
                    "McKinsey", "Forrester")


@pytest.mark.parametrize("name", VARIANTS)
def test_no_research_statistics_in_template_text(name):  # §3.1/§2.1: the report cites no research figures
    text = VARIANTS[name]
    body = template_text(text, markdown_export(text))
    for source in RESEARCH_SOURCES:
        assert not re.search(rf"\b{source}\b", body), source
    assert "%" not in body


SECTION5 = """METHOD
DIA scores each intake on four dimensions (urgency, budget fit, technical readiness,
strategic value), 0–5 each, 20 maximum. Value at stake is hours per week × loaded
hourly rate × 48 weeks, from the client's own answers. Tier, price range and timeline
come from DIA's own rules. This report is generated from fixed templates; no AI-written
text appears in it.

SOURCES
Data: Northstar Studio discovery questionnaire, received October 23, 2026.
Presentation conventions informed by:
  · Minto, B. The Minto Pyramid Principle (answer-first structure).
  · Zelazny, G. Say It With Charts, 4th ed., McGraw-Hill, 2001.
  · Google, Material Design data visualization guidelines; People + AI Guidebook.
  · Microsoft Writing Style Guide; Apple Human Interface Guidelines.
The analysis and recommendation are DIA's own. These works shaped the layout, not the
conclusions. Their authors and publishers have not reviewed or endorsed DIA or this
report. Names are trademarks of their owners."""  # copied from dia-savant-proposal.md §5


def check_sources(text: str) -> None:
    flat = normalise(text)
    assert normalise(SECTION5) in flat
    assert flat.index("Data: Northstar Studio discovery questionnaire") < flat.index("Minto, B.")
    for name in ("OpenAI", "Anthropic", "SpaceX", "xAI", "Grok", "Claude", "ChatGPT"):
        assert name not in text


def test_markdown_sources_match_section_5_verbatim():  # §8.10 (Markdown)
    check_sources(markdown_export(SAMPLE))
    report, _, _ = report_for(SAMPLE)
    assert report["sources"][0] == f"Data: Northstar Studio discovery questionnaire, received {DATE}."
    assert report["sources"][1:] == list(report_model.WORKS)


def test_scrub_keeps_savant_documents_and_cuts_appended_text():
    intake = parse_questionnaire_markdown(SAMPLE)
    docs = generate_documents(intake, score_opportunity(intake), report_date=DATE)
    assert scrub_for_export(docs) == docs
    tainted = dict(docs)
    tainted["opportunity-analysis.md"] += "\nFAKE-GEMINI-7Q\n\n## Strategic analysis\nmore AI text\n"
    assert scrub_for_export(tainted) == docs
    no_value = generate_documents(parse_questionnaire_markdown(questionnaire(q1="", q2="")), score_opportunity(
        parse_questionnaire_markdown(questionnaire(q1="", q2=""))), report_date=DATE)
    assert scrub_for_export(no_value) == no_value


# --- §8.5 Answer-first PDF ------------------------------------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app import build_intake_response as app_build  # noqa: E402

PDF_ORDER = MIN_ORDER


def pdf_text_of(body: bytes, layout: bool = False) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    args = ["pdftotext", *(["-layout"] if layout else []), "-", "-"]
    return subprocess.run(args, input=body, capture_output=True, check=True).stdout.decode()


@pytest.fixture(scope="module")
def pdfs():
    pytest.importorskip("weasyprint")
    from client_discovery.report import render_html, render_pdf

    out = {}
    for name, text in VARIANTS.items():
        result = app_build(text)
        out[name] = {"text": text, "html": render_html(result, report_date=DATE),
                     "pdf": pdf_text_of(render_pdf(result, report_date=DATE))}
    return out


def strip_running_heads(text: str) -> str:
    """Drop the page header/footer strings (uppercase mono) that WeasyPrint repeats on every page."""
    heads = re.compile(r"^(DISCOVERYINTAKEAGENT|PAGE\d+OF\d+|MADEBYBASH|NORTHSTARSTUDIO|THECLIENT|OCTOBER\d+,\d{4})")
    return "\n".join(line for line in text.splitlines() if not heads.match(line.replace(" ", "")))


@pytest.mark.parametrize("name", VARIANTS)
def test_pdf_section_order_matches_3_1(name, pdfs):  # §8.8
    html = pdfs[name]["html"]
    labels = re.findall(r'<h2 class="section-title"><span class="section-no">(\d\d)</span>([^<]+)</h2>', html)
    assert [label for _, label in labels] == PDF_ORDER
    assert [no for no, _ in labels] == [f"{i:02d}" for i in range(1, 10)]
    lines = [line.strip() for line in pdfs[name]["pdf"].splitlines()]
    positions, start = [], 0
    for label in PDF_ORDER:  # each section label appears as its own line, in §3.1 order
        start = lines.index(label, start)
        positions.append(start)
    assert positions == sorted(positions)
    assert positions[0] < lines.index("What is missing") < lines.index("Client profile")
    cover = normalise(" ".join(lines[:positions[0]]))
    assert "TIER" in cover and "SCORE" in cover
    report, _, score = report_for(pdfs[name]["text"])
    assert normalise(report["cover_sentence"]) in cover
    assert f"{score.total_score} /{score.max_score}" in cover


@pytest.mark.parametrize("name", VARIANTS)
def test_no_inputs_label_in_pdf(name, pdfs):  # §8.2 (PDF)
    assert "Inputs:" not in pdfs[name]["pdf"] and "Inputs:" not in pdfs[name]["html"]
    for word in ("complete", "partial", "thin"):
        assert f"Inputs: {word}" not in pdfs[name]["pdf"]


def test_pdf_cover_and_titles_name_the_answer(pdfs):  # §8.6 (PDF)
    flat = normalise(pdfs["sample"]["pdf"])
    for title in ("Northstar Studio should start with a Custom AI Agent ($2,500-$10,000, 2-4 weeks).",
                  "0 missing answers block a firm quote",
                  "Northstar Studio fits a Custom AI Agent at $2,500-$10,000 over 2-4 weeks",
                  "Urgency is the weakest signal at 3 of 5; budget fit leads at 4",
                  "Northstar Studio loses about $36,960 a year to 3 manual workflows"):
        assert title in flat, title
    legacy = normalise(pdfs["legacy"]["pdf"])
    assert "2 missing answers block a firm quote: value inputs and success metric" in legacy
    assert "Northstar Studio fits a Custom AI Agent, pending weekly hours and an hourly cost." in legacy


@pytest.mark.parametrize("name", VARIANTS)
def test_pdf_headings_sentence_case_no_contractions_no_we(name, pdfs):  # §8.9 (PDF)
    html = pdfs[name]["html"]
    report, _, _ = report_for(pdfs[name]["text"])
    headings = re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", html, re.S)
    for heading in headings:
        plain = normalise(re.sub(r"<[^>]+>", " ", heading))
        plain = re.sub(r"^\d\d ", "", plain)
        if plain in (report["client"], "Untitled client"):
            continue
        assert is_sentence_case(plain, report["client"]), plain
    body = template_text(pdfs[name]["text"], strip_running_heads(pdfs[name]["pdf"]))
    body = body.replace(normalise(report["method_and_sources"]), " ")
    flat = normalise(body)
    method = normalise(report["method_and_sources"])
    flat = flat.replace(method, " ")
    for client_value in ("client's own answers", "DIA's own"):  # inside the verbatim §5 block
        flat = flat.replace(client_value, " ")
    assert not CONTRACTION.search(flat), CONTRACTION.search(flat)
    assert not re.search(r"\bwe\b|\bour\b", flat, re.I)
    assert "!" not in flat


def test_pdf_sources_match_section_5_verbatim(pdfs):  # §8.10 (PDF)
    check_sources(strip_running_heads(pdfs["sample"]["pdf"]))
    html = pdfs["sample"]["html"]
    import html as html_lib

    assert normalise(SECTION5) in normalise(html_lib.unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.mark.parametrize("name", VARIANTS)
def test_pdf_has_exactly_one_exhibit_built_to_3_4(name, pdfs):  # §3.4
    html = pdfs[name]["html"]
    assert html.count('<figure class="exhibit"') == 1 and html.count("Exhibit 1") == 1 and "Exhibit 2" not in html
    exhibit = html[html.index('<figure class="exhibit"'):html.index("</figure>")]
    order = [exhibit.index(s) for s in ("Exhibit 1", 'class="action-title"', "Sub-scores, points out of 5",
                                        'class="bars"', "Score ", "Source: ")]
    assert order == sorted(order)
    labels = re.findall(r'class="bar-label">([^<]+)', exhibit)
    assert [label.strip() for label in labels] == ["Urgency", "Budget fit", "Readiness", "Strategic value"]
    values = re.findall(r'class="bar-value">([^<]+)<', exhibit)
    assert len(values) == 4 and all(re.fullmatch(r"[0-5]/5( default)?", v) for v in values)  # text equivalent
    assert exhibit.count("is-highlight") <= 1
    if "is-highlight" in exhibit:
        assert "(weakest)" in exhibit  # never colour alone
    assert "legend" not in exhibit.lower()
    report, _, score = report_for(pdfs[name]["text"])
    assert f"Score {score.total_score} of 20" in exhibit
    assert f"Source: {report['client']} discovery questionnaire, {DATE}; DIA scoring rules v2" in exhibit
    if score.strategic_value_default:
        assert values[3] == "3/5 default"
        row = exhibit[exhibit.rindex("<tr", 0, exhibit.index("3/5 default")):exhibit.index("3/5 default")]
        assert "is-highlight" not in row


@pytest.mark.parametrize("name", VARIANTS)
def test_no_research_statistics_in_pdf(name, pdfs):  # §3.1: no research stats
    body = template_text(pdfs[name]["text"], strip_running_heads(pdfs[name]["pdf"]))
    for source in RESEARCH_SOURCES:
        assert not re.search(rf"\b{source}\b", body), source
    assert "%" not in body


def test_pdf_ignores_client_supplied_documents_and_scores():
    pytest.importorskip("weasyprint")
    from client_discovery.report import render_pdf

    result = app_build(SAMPLE)
    forged = json.loads(json.dumps(result))
    forged["documents"] = {k: "# Forged\n\nFORGED-DOC-TEXT" for k in forged["documents"]}
    forged["score"]["total_score"] = 19
    forged["strategic_analysis"] = "FAKE-GEMINI-7Q"
    text = pdf_text_of(render_pdf(forged, report_date=DATE))
    assert "FORGED-DOC-TEXT" not in text and "FAKE-GEMINI-7Q" not in text
    assert "Score 15 of 20" in normalise(text)


# --- §8.1 No AI text in any export, with or without GEMINI_API_KEY ---------------------------------
# (Persona v1.2 follow-up 2 covers tainted results on every export path: tests/test_strategic_analysis_exports.py.)

AI_FAKE = "FAKE-GEMINI-SAVANT-9Z: a strategic read written by a model."


@pytest.mark.parametrize("key", [None, "fake-key"], ids=["without-key", "with-key"])
@pytest.mark.parametrize("name", ["sample", "legacy"])
def test_no_ai_text_or_agent_error_in_any_export(key, name, monkeypatch):  # §8.1
    pytest.importorskip("weasyprint")
    try:
        from client_discovery import service
    except Exception as exc:  # pragma: no cover - the agent stack is optional
        pytest.skip(f"service path unavailable: {exc}")
    from client_discovery.report import render_pdf

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    with patch.object(service, "root_agent", SimpleNamespace(run=lambda prompt: SimpleNamespace(text=AI_FAKE))), \
            patch.object(service, "trigger_obs_screenshot", lambda *a, **k: ""), \
            patch.object(service, "save_obs_replay_buffer", lambda *a, **k: ""):
        result = service.build_intake_response(VARIANTS[name])
    expected = AI_FAKE if key else "DIA Agent Error: GEMINI_API_KEY is missing or invalid."
    assert result["strategic_analysis"] == expected  # the path really produced AI text / the error
    safe = scrub_for_export(result)
    md = "\n\n---\n\n".join(safe["documents"][n] for n in report_model.MD_EXPORT_ORDER)
    exports = {"md": md, "json": json.dumps(safe), "pdf": pdf_text_of(render_pdf(result, report_date=DATE))}
    for where, body in exports.items():
        assert "FAKE-GEMINI-SAVANT-9Z" not in body, where
        assert "dia agent error" not in body.lower(), where
        assert "strategic_analysis" not in body, where


# --- §8.6 On screen: Inputs label, flags to clear first, JSON fields, cabinet chip ------------------

from tests.test_persona_v12 import base_url, browser, context, open_page, run_intake  # noqa: E402,F401


def test_json_carries_value_flags_sources_and_inputs_confidence():  # §6
    result = app_build(SAMPLE)
    assert result["value_at_stake"] == 36960 and result["flags"] == ["TRUST_REPAIR"]
    assert result["inputs_confidence"] == "partial"
    assert result["sources"][0].startswith("Data: Northstar Studio discovery questionnaire, received ")
    assert result["sources"][1:] == list(report_model.WORKS)
    assert result["flag_actions"] == [report_model.FLAG_ACTIONS["TRUST_REPAIR"]]
    assert app_build(LEGACY)["inputs_confidence"] == "thin"
    assert app_build(questionnaire(q9="None"))["inputs_confidence"] == "complete"
    assert "strategic_analysis" not in result


def test_inputs_sentences_match_between_python_and_js():
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    for state, sentence in report_model.INPUTS_SENTENCES.items():
        assert f'{state}: "{sentence}"' in js


@pytest.mark.parametrize("name, state", [("sample", "partial"), ("clean", "complete"), ("legacy", "thin"), ("thin", "thin")])
def test_screen_shows_inputs_label_with_exactly_one_state(name, state, context, base_url):  # §8.2 (screen)
    page = open_page(context, base_url)
    run_intake(page, VARIANTS[name])
    label = page.inner_text("#inputsState")
    assert page.is_visible("#inputsState") and page.is_visible("#inputsNote")
    assert re.fullmatch(r"inputs: (complete|partial|thin)", label.strip().lower()), label
    assert label.strip().lower() == f"inputs: {state}"
    assert page.inner_text("#inputsNote") == report_model.INPUTS_SENTENCES[state]
    actions = page.eval_on_selector_all("#flagActions li", "els => els.map(e => e.textContent)")
    assert actions == app_build(VARIANTS[name])["flag_actions"]
    assert page.is_visible("#flagsHeading") == bool(actions)
    # Exports never carry the label.
    md = page.evaluate("buildMarkdownExport()")
    assert "Inputs:" not in md
    assert re.findall(r"(?m)^## (.+)$", md) == MIN_ORDER
    assert page.errors == []


def test_stored_result_without_savant_fields_hides_the_label(context, base_url):
    page = open_page(context, base_url)
    run_intake(page, SAMPLE)
    page.evaluate("() => { const p = JSON.parse(JSON.stringify(latestPayload)); delete p.inputs_confidence; delete p.flag_actions; showResults(p, ''); }")
    assert page.is_hidden("#inputsState") and page.is_hidden("#flagActions")


def test_max_score_20_and_cabinet_score_chip_unchanged(context, base_url):  # §8.11
    import subprocess as sp

    page = open_page(context, base_url)
    page.evaluate("() => localStorage.clear()")
    run_intake(page, SAMPLE)
    page.evaluate("() => CabinetDrawer.open()")
    page.wait_for_selector(".drawer-card .card-meta")
    meta = page.inner_text(".drawer-card .card-meta")
    assert re.search(r" · Custom AI Agent · 15/20$", meta), meta
    # The chip's rendering line is byte-for-byte what it was before dia-savant (b91cd5a).
    line = 'article.querySelector(".card-meta").textContent = `${whenOf(item.savedAt)} · ${item.tier} · ${item.score}/${item.maxScore}`;'
    assert line in (ROOT / "static/app.js").read_text(encoding="utf-8")
    try:
        before = sp.run(["git", "show", "b91cd5a:static/app.js"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (sp.CalledProcessError, FileNotFoundError):
        before = line
    assert line in before
    assert page.errors == []
