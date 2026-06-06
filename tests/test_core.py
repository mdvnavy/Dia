from pathlib import Path

from client_discovery.core import (
    generate_documents,
    parse_questionnaire_markdown,
    score_opportunity,
    validate_intake,
)


FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_questionnaire_extracts_company_profile_and_signals():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))

    assert intake.company_name == "Northstar Studio"
    assert intake.website == "https://northstar.example"
    assert intake.industry == "B2B services"
    assert intake.size == "12"
    assert intake.tools == "Google Workspace, HubSpot, Slack"
    assert len(intake.pain_points) == 2
    assert len(intake.goals) == 2
    assert intake.decision_maker == "Founder"


def test_validate_intake_flags_missing_submission_critical_fields():
    intake = parse_questionnaire_markdown(read_fixture("missing_fields_questionnaire.md"))

    issues = validate_intake(intake)
    issue_codes = {issue.code for issue in issues}

    assert {"missing_budget", "missing_decision_maker", "missing_start_date"} <= issue_codes
    assert all(issue.severity in {"warning", "error"} for issue in issues)


def test_score_opportunity_returns_tier_and_transparent_reasons():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))

    score = score_opportunity(intake)

    assert score.tier == "Custom AI Agent"
    assert score.total_score == 12
    assert score.max_score == 20
    assert score.price_range == "$2,500-$10,000"
    assert any("2 pain point" in reason for reason in score.reasons)


def test_generate_documents_returns_public_safe_markdown_outputs():
    intake = parse_questionnaire_markdown(read_fixture("complete_questionnaire.md"))
    score = score_opportunity(intake)

    documents = generate_documents(intake, score)

    assert set(documents) == {
        "client-profile.md",
        "opportunity-analysis.md",
        "proposal-draft.md",
    }
    combined = "\n".join(documents.values())
    assert "Northstar Studio" in combined
    assert "Custom AI Agent" in combined
    assert "billin" not in combined.lower()
