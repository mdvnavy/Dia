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
    assert "Why this tier:\n- Urgency 3/5:" in summary and "\n- Budget fit 4/5:" in summary and "\n- Readiness 4/5:" in summary
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
