from pathlib import Path

from character import (
    generate_intake_documents,
    parse_intake,
    score_client_opportunity,
    validate_intake_fields,
)


FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_intake_returns_dictionary():
    fixture = read_fixture("complete_questionnaire.md")
    result = parse_intake(fixture)

    assert isinstance(result, dict)
    assert result.get("company_name") == "Northstar Studio"
    assert result.get("industry") == "B2B services"
    assert "tools" in result


def test_validate_intake_fields_returns_list_of_dictionaries():
    fixture = read_fixture("missing_fields_questionnaire.md")
    result = validate_intake_fields(fixture)

    assert isinstance(result, list)
    assert len(result) > 0
    assert isinstance(result[0], dict)

    codes = {issue.get("code") for issue in result}
    assert "missing_budget" in codes
    assert "missing_start_date" in codes


def test_score_client_opportunity_returns_dictionary():
    fixture = read_fixture("complete_questionnaire.md")
    result = score_client_opportunity(fixture)

    assert isinstance(result, dict)
    assert result.get("tier") == "Custom AI Agent"
    assert result.get("total_score") == 12
    assert "reasons" in result


def test_generate_intake_documents_returns_dictionary():
    fixture = read_fixture("complete_questionnaire.md")
    result = generate_intake_documents(fixture)

    assert isinstance(result, dict)
    assert "client-profile.md" in result
    assert "opportunity-analysis.md" in result
    assert "proposal-draft.md" in result

    assert "Northstar Studio" in result["client-profile.md"]
