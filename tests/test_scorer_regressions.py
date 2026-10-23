"""Regression tests from the 2026-10-23 audit's sample intakes.

The three fixtures in tests/fixtures/samples/ are fictional. Each exposed a
scorer bug in main@611d6df:

- dental:    "about 5k" scored as $5 (poor fit); PHIPA and "ASAP" ignored.
- logistics: "$50,000+" scored as a poor fit; SOC 2 ignored.
- sparse:    unnumbered "## Pain points" / "## Goals" parsed as empty, so a
             1-pain, no-tools, $1,500 intake was sold a Custom AI Agent.
"""

from pathlib import Path

import pytest

from app import build_intake_response
from client_discovery.core import (
    _assess_budget,
    _extract_topic_answers,
    GOAL_HEADING,
    PAIN_POINT_HEADING,
    generate_documents,
    parse_budget,
    parse_questionnaire_markdown,
    score_opportunity,
)
from client_discovery.models import ClientIntake

SAMPLES = Path(__file__).parent / "fixtures" / "samples"
INF = float("inf")


def _run(name: str) -> dict:
    return build_intake_response((SAMPLES / name).read_text(encoding="utf-8"))


# --- budget parsing -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("about 5k", (5000, 5000)),
        ("$5,000", (5000, 5000)),
        ("$50,000+", (50000, INF)),
        ("over 50k", (50000, INF)),
        ("at least $20,000", (20000, INF)),
        ("5-10k", (5000, 10000)),
        ("$5k - $10k", (5000, 10000)),
        ("$2,500\u2013$10,000", (2500, 10000)),
        ("1.5k", (1500, 1500)),
        ("$1M", (1_000_000, 1_000_000)),
        ("under 5k", (0, 5000)),
        ("up to $10,000", (0, 10000)),
        ("not sure, maybe $1,500", (1500, 1500)),
        ("10 thousand", (10000, 10000)),
    ],
)
def test_parse_budget(text, expected):
    assert parse_budget(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "TBD", "flexible"])
def test_parse_budget_without_amount(text):
    assert parse_budget(text) is None


@pytest.mark.parametrize(
    ("budget", "tier", "fit"),
    [
        ("about 5k", "Custom AI Agent", 3),         # was 2
        ("$50,000+", "Full Integration", 4),        # was 2: above the top tier
        ("5-10k", "Custom AI Agent", 3),
        ("$15,000", "Custom AI Agent", 3),          # above, within 2x: room for scope
        ("$500,000", "Quick Win", 2),               # far above: re-check scope
        ("$1,000", "Custom AI Agent", 2),           # below the band
        ("maybe $1,500", "Quick Win", 4),
        ("", "Quick Win", 2),
    ],
)
def test_budget_fit(budget, tier, fit):
    assert _assess_budget(budget, tier)[0] == fit


# --- section parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    ["## 3. Pain Points", "## Pain points", "## 3) Pain points", "### Pain Points", "## PAIN POINTS & CHALLENGES"],
)
def test_pain_point_heading_variants(heading):
    md = f"""{heading}
| Question | Answer |
| --- | --- |
| Biggest headache | Inquiries get lost. |

## Goals
| Question | Answer |
| --- | --- |
| Success | Reply within a day. |
"""
    assert _extract_topic_answers(md, PAIN_POINT_HEADING) == ["Inquiries get lost."]
    assert _extract_topic_answers(md, GOAL_HEADING) == ["Reply within a day."]


def test_section_stops_at_next_heading_but_keeps_subheadings():
    md = """## Pain points
| Question | Answer |
| --- | --- |
| A | first |
### Detail
| Question | Answer |
| --- | --- |
| B | second |
## Budget
| Question | Answer |
| --- | --- |
| Budget range | 5k |
"""
    assert _extract_topic_answers(md, PAIN_POINT_HEADING) == ["first", "second"]


def test_pain_point_word_inside_a_question_is_not_a_heading():
    md = """## Company
| Question | Answer |
| --- | --- |
| Pain points | n/a |
"""
    assert _extract_topic_answers(md, PAIN_POINT_HEADING) == []


# --- sample intakes -------------------------------------------------------


def test_dental_sample_budget_compliance_and_urgency():
    result = _run("dental_phipa_intake.md")
    score = result["score"]
    assert score["tier"] == "Custom AI Agent"
    assert score["budget_fit"] == 3          # "about 5k" fits the $2.5k-$10k band
    assert score["urgency"] == 4             # 2 pains + "ASAP"
    assert any("ASAP" in reason for reason in score["reasons"])
    assert any("PHIPA" in reason for reason in score["reasons"])
    codes = [issue["code"] for issue in result["issues"]]
    assert codes == ["compliance_review"]
    assert all(issue["severity"] != "error" for issue in result["issues"])
    proposal = result["documents"]["proposal-draft.md"]
    assert "PHIPA" in proposal
    assert "Front desk spends ~2 hrs/day" in proposal
    assert "Cut no-shows below 5%" in proposal
    assert "Dentrix" in proposal


def test_logistics_sample_large_budget_and_soc2():
    result = _run("logistics_soc2_intake.md")
    score = result["score"]
    assert score["tier"] == "Full Integration"
    assert score["budget_fit"] == 4          # $50,000+ exceeds the top tier
    assert any("above the tier" in reason for reason in score["reasons"])
    assert any("SOC 2" in reason for reason in score["reasons"])
    assert "compliance_review" in [issue["code"] for issue in result["issues"]]
    assert "SOC 2" in result["documents"]["proposal-draft.md"]


def test_sparse_sample_parses_unnumbered_sections_and_is_a_quick_win():
    result = _run("sparse_unnumbered_intake.md")
    intake, score = result["intake"], result["score"]
    assert intake["pain_points"] == [
        "Wedding inquiries come through Instagram DMs and we lose track."
    ]
    assert intake["goals"] == ["Every inquiry gets a reply and a quote within a day."]
    assert score["tier"] == "Quick Win"      # 1 pain, no tools, ~$1,500
    assert score["budget_fit"] == 4
    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_pain_points" not in codes
    assert "missing_goals" not in codes
    assert {"missing_decision_maker", "missing_start_date"} <= codes
    proposal = result["documents"]["proposal-draft.md"]
    assert "On hold" not in proposal
    assert "Before kickoff, confirm:" in proposal


def test_error_level_issue_forces_quick_win_and_holds_the_proposal():
    md = (SAMPLES / "sparse_unnumbered_intake.md").read_text(encoding="utf-8")
    # Remove the pain-point section entirely -> error-level issue.
    start = md.index("## Pain points")
    end = md.index("## Goals")
    md = md[:start] + md[end:]
    md = md.replace("| Industry | Retail florist + weddings |", "| Industry | Retail florist + weddings |\n| Tools | Shopify, Instagram |")

    result = build_intake_response(md)
    assert any(issue["severity"] == "error" for issue in result["issues"])
    assert result["score"]["tier"] == "Quick Win"  # was Custom AI Agent
    assert any("provisional" in reason for reason in result["score"]["reasons"])
    proposal = result["documents"]["proposal-draft.md"]
    assert "On hold" in proposal
    assert "At least one pain point is required" in proposal
    for section in ("## Executive Summary", "## Recommended Solution", "## Delivery Plan", "## Investment", "## Next Step"):
        assert section in proposal


# --- executive summary ----------------------------------------------------


def test_executive_summary_is_not_boilerplate_and_differs_per_client():
    summaries = []
    for name in ("dental_phipa_intake.md", "logistics_soc2_intake.md", "sparse_unnumbered_intake.md"):
        proposal = _run(name)["documents"]["proposal-draft.md"]
        summary = proposal.split("## Executive Summary", 1)[1].split("##", 1)[0]
        assert "turn discovery intake into a qualified opportunity package" not in summary
        summaries.append(summary)
    assert len(set(summaries)) == 3


def test_executive_summary_is_deterministic():
    intake = parse_questionnaire_markdown((SAMPLES / "dental_phipa_intake.md").read_text(encoding="utf-8"))
    score = score_opportunity(intake)
    assert generate_documents(intake, score) == generate_documents(intake, score)


def test_urgency_ignores_non_urgent_start_and_caps_at_5():
    calm = ClientIntake(pain_points=["a", "b"], goals=["g"], start_date="Q2 next year")
    assert score_opportunity(calm).urgency == 2
    rush = ClientIntake(pain_points=["a"] * 5, goals=["g"], start_date="ASAP")
    assert score_opportunity(rush).urgency == 5
