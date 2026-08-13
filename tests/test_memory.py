import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
venv_site_packages = os.path.join(project_root, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

import pytest

from client_discovery import memory
from client_discovery.models import ClientIntake, OpportunityScore


SCORE = OpportunityScore(
    tier="Tier 2",
    scope="scope",
    price_range="$10k",
    timeline="6 weeks",
    urgency=3,
    budget_fit=3,
    tech_readiness=2,
    strategic_value=3,
    total_score=11,
    max_score=16,
    reasons=["r"],
)

INTAKE = ClientIntake(
    company_name="Acme Corp",
    website="https://www.acme.example",
    industry="Manufacturing",
    pain_points=["manual invoicing"],
    goals=["automate ops"],
    budget="$10k-$25k",
)


class SheetCapture:
    """Stub for memory._sheet_request that records calls and serves reads."""

    def __init__(self, tabs=None):
        self.tabs = tabs or {}
        self.calls = []
        self.fail_on = None

    def __call__(self, method, url, token, payload=None):
        self.calls.append((method, url, payload))
        if self.fail_on and self.fail_on in url:
            raise RuntimeError("sheet unreachable")
        if method == "GET":
            for tab, rows in self.tabs.items():
                if f"/values/{tab}" in url:
                    return {"values": rows}
            return {}
        return {}


@pytest.fixture
def sheet(monkeypatch):
    capture = SheetCapture()
    monkeypatch.setenv("MEMORY_SHEET_ID", "sheet123")
    monkeypatch.setattr(memory, "_access_token", lambda: "tok")
    monkeypatch.setattr(memory, "_sheet_request", capture)
    return capture


# --- identity -----------------------------------------------------------


def test_client_id_normalizes_name_and_domain():
    assert (
        memory.client_id_for("Acme Corp!", "https://www.Acme.Example/path")
        == "acme-corp|acme.example"
    )


def test_client_id_without_website_is_name_only():
    assert memory.client_id_for("  Acme   Corp  ") == "acme-corp"


def test_client_id_bare_domain_without_scheme():
    assert memory.client_id_for("Acme", "www.acme.example") == "acme|acme.example"


# --- disabled = no-op ----------------------------------------------------


def test_unset_sheet_id_disables_everything(monkeypatch):
    monkeypatch.delenv("MEMORY_SHEET_ID", raising=False)
    monkeypatch.setattr(
        memory, "_access_token", lambda: pytest.fail("must not fetch a token")
    )
    assert memory.is_enabled() is False
    assert memory.read_client_history("Acme Corp") is None
    memory.record_engagement(INTAKE, SCORE, source="/api/process")  # no raise


def test_recall_summary_reports_unknown_when_disabled(monkeypatch):
    monkeypatch.delenv("MEMORY_SHEET_ID", raising=False)
    assert memory.recall_summary("Acme Corp")["known_client"] is False


# --- write path ----------------------------------------------------------


def test_record_engagement_writes_ledger_first_with_insert_rows(sheet):
    memory.record_engagement(INTAKE, SCORE, source="/api/process", documents=["a.md"])

    appends = [c for c in sheet.calls if ":append" in c[1]]
    assert len(appends) == 2  # engagements ledger + new client rollup
    # Ordering: durable ledger row first, rollup second.
    assert "engagements:append" in appends[0][1]
    assert "clients:append" in appends[1][1]
    # The proof-mandated primitive on every append.
    assert all("insertDataOption=INSERT_ROWS" in c[1] for c in appends)

    ledger_row = appends[0][2]["values"][0]
    assert ledger_row[1] == "acme-corp|acme.example"
    assert ledger_row[3] == "/api/process"
    assert ledger_row[11] == "pending"  # outcome set later by the operator


def test_record_engagement_updates_existing_client_rollup(sheet):
    sheet.tabs = {
        "clients": [
            memory.CLIENTS_HEADERS,
            [
                "acme-corp|acme.example",
                "Acme Corp",
                "https://acme.example",
                "Manufacturing",
                "2026-07-01T00:00:00+00:00",
                "2026-07-01T00:00:00+00:00",
                "2",
                "Tier 3",
                "8",
                "proposal_sent",
                "2026-07-01T00:00:00+00:00",
            ],
        ]
    }
    memory.record_engagement(INTAKE, SCORE, source="/api/agent")

    updates = [c for c in sheet.calls if c[0] == "PUT"]
    assert len(updates) == 1
    assert "clients" in updates[0][1]
    row = updates[0][2]["values"][0]
    assert row[6] == 3  # engagement_count incremented
    assert row[4] == "2026-07-01T00:00:00+00:00"  # first_seen preserved
    assert row[9] == "proposal_sent"  # operator-set status preserved
    assert row[7] == "Tier 2"  # rollup reflects latest score


def test_ledger_failure_skips_rollup_and_never_raises(sheet):
    sheet.fail_on = "engagements:append"
    memory.record_engagement(INTAKE, SCORE, source="/api/process")
    assert not any("clients" in c[1] for c in sheet.calls if c[0] != "GET")


def test_rollup_failure_after_ledger_never_raises(sheet):
    sheet.fail_on = "clients"
    memory.record_engagement(INTAKE, SCORE, source="/api/process")
    assert any("engagements:append" in c[1] for c in sheet.calls)


def test_record_without_company_name_is_noop(sheet):
    memory.record_engagement(ClientIntake(), SCORE, source="/api/process")
    assert sheet.calls == []


# --- read path -----------------------------------------------------------


def _sheet_with_history():
    return {
        "clients": [
            memory.CLIENTS_HEADERS,
            ["other|x.example", "Other", "x.example", "", "", "", "1", "", "", "", ""],
            [
                "acme-corp|acme.example",
                "Acme Corp",
                "acme.example",
                "Manufacturing",
                "2026-07-01T00:00:00+00:00",
                "2026-07-05T00:00:00+00:00",
                "4",
                "Tier 2",
                "11",
                "prospect",
                "2026-07-05T00:00:00+00:00",
            ],
        ],
        "engagements": [
            memory.ENGAGEMENTS_HEADERS,
            *[
                [f"e{i}", "acme-corp|acme.example", f"2026-07-0{i}T00:00:00+00:00", "/api/process", "Tier 2", "11", "16", "[]", "[]", "$10k", "[]", "pending", ""]
                for i in range(1, 5)
            ],
            ["ex", "other|x.example", "2026-07-05T00:00:00+00:00", "/api/process", "Tier 1", "14", "16", "[]", "[]", "", "[]", "pending", ""],
        ],
    }


def test_read_history_matches_composite_key_and_caps_engagements(sheet):
    sheet.tabs = _sheet_with_history()
    history = memory.read_client_history("Acme Corp", "https://acme.example")
    assert history["client"]["engagement_count"] == "4"
    assert len(history["engagements"]) == memory.HISTORY_LIMIT
    assert [e["engagement_id"] for e in history["engagements"]] == ["e2", "e3", "e4"]


def test_read_history_falls_back_to_name_only_match(sheet):
    sheet.tabs = _sheet_with_history()
    history = memory.read_client_history("ACME CORP")  # no website supplied
    assert history is not None
    assert history["client"]["client_id"] == "acme-corp|acme.example"


def test_read_history_unknown_company_returns_none(sheet):
    sheet.tabs = _sheet_with_history()
    assert memory.read_client_history("Nobody Inc") is None


def test_read_history_sheet_error_returns_none(sheet):
    sheet.fail_on = "clients"
    assert memory.read_client_history("Acme Corp") is None


def test_recall_summary_known_client(sheet):
    sheet.tabs = _sheet_with_history()
    summary = memory.recall_summary("Acme Corp", "acme.example")
    assert summary["known_client"] is True
    assert summary["client"]["last_tier"] == "Tier 2"


# --- agent wiring --------------------------------------------------------


def test_build_agent_includes_recall_tool_only_when_enabled(monkeypatch):
    import character

    monkeypatch.setenv("MEMORY_SHEET_ID", "sheet123")
    agent = character.build_agent()
    assert character.recall_client_history in agent.tools

    monkeypatch.delenv("MEMORY_SHEET_ID")
    agent = character.build_agent()
    assert character.recall_client_history not in agent.tools
