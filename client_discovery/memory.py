"""Sheets-backed client memory for DIA.

Implements docs/2026-07-07_client-memory-layer_design.md: a Google Sheet in
the *operator's own* Google account acts as DIA's client memory. Two tabs:

    clients      one row per known company (identity + rollup)
    engagements  one row per intake run (append-only ledger)

Design constraints carried over from the sheets-mini-db proof
(agent-eggs/sheets-mini-db/PROOF.md):

- Writes use ``insertDataOption=INSERT_ROWS`` only. Naive append (OVERWRITE)
  silently loses concurrent writes (9/12 lost in the 12-writer test);
  INSERT_ROWS survived 12/12.
- The engagements row is written FIRST, the clients rollup second. Sheets has
  no cross-row transactions, so if the rollup update fails the durable
  ledger is still intact and the rollup is recomputable — nothing is lost,
  only briefly stale.
- Memory is an enhancement, never a dependency: MEMORY_SHEET_ID unset means
  every function is a silent no-op, and any API/auth failure degrades to a
  logged warning — the same posture app.py takes for a missing Gemini key.

Client data never touches the HF skill registry or any shared
infrastructure; it lives only in the operator's Sheet.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from client_discovery.models import ClientIntake, OpportunityScore

logger = logging.getLogger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
# Sheets reads run ~0.6-1.4s per the proof; a stuck call must fall back to
# "no history" rather than pin an intake request.
REQUEST_TIMEOUT_SECONDS = 8
# Cap recalled engagements to bound agent context size and read volume.
HISTORY_LIMIT = 3

CLIENTS_TAB = "clients"
ENGAGEMENTS_TAB = "engagements"

CLIENTS_HEADERS = [
    "client_id",
    "company_name",
    "website",
    "industry",
    "first_seen_at",
    "last_seen_at",
    "engagement_count",
    "last_tier",
    "last_total_score",
    "status",
    "updated_at",
]

ENGAGEMENTS_HEADERS = [
    "engagement_id",
    "client_id",
    "timestamp",
    "source",
    "tier",
    "total_score",
    "max_score",
    "pain_points",
    "goals",
    "budget",
    "documents_generated",
    "proposal_outcome",
    "notes",
]


def memory_sheet_id() -> str:
    return os.environ.get("MEMORY_SHEET_ID", "").strip()


def is_enabled() -> bool:
    """Memory is on only when MEMORY_SHEET_ID is set, mirroring MAKE_MCP_URL."""
    return bool(memory_sheet_id())


def client_id_for(company_name: str, website: str = "") -> str:
    """Derive a stable client id from normalized name + website domain.

    Composite key per the spec's Open Question #1 resolution: simplest
    approach, no new dependency. Ambiguous matches surface as duplicate rows
    the operator can merge in the Sheet — human-in-the-loop cleanup is a
    designed-in property of the store, not a failure.
    """
    name_part = re.sub(r"[^a-z0-9]+", "-", company_name.strip().lower()).strip("-")
    domain = urlparse(website if "//" in website else f"//{website}").netloc
    domain_part = domain.lower().removeprefix("www.")
    return f"{name_part}|{domain_part}" if domain_part else name_part


def _access_token() -> str | None:
    """Fetch an ADC bearer token, same pattern as character._gcp_mcp_toolset."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as error:
        logger.warning("client memory disabled, google-auth missing: %s", error)
        return None
    try:
        credentials, _ = google.auth.default(scopes=[SHEETS_SCOPE])
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception as error:  # noqa: BLE001 - memory must never break intake
        logger.warning("client memory disabled, ADC unavailable: %s", error)
        return None
    return credentials.token


def _sheet_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    import requests

    response = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def _read_tab(sheet_id: str, token: str, tab: str) -> list[list[str]]:
    url = f"{SHEETS_API}/{sheet_id}/values/{quote(tab)}"
    return _sheet_request("GET", url, token).get("values", [])


def _rows_as_dicts(rows: list[list[str]], headers: list[str]) -> list[dict[str, str]]:
    """Map sheet rows to dicts by the tab's actual header row.

    The first row of the tab is trusted as the header so operator-added
    columns (or reordered ones) don't corrupt field mapping.
    """
    if not rows:
        return []
    actual_headers = [h.strip() for h in rows[0]] or headers
    out = []
    for row in rows[1:]:
        padded = row + [""] * (len(actual_headers) - len(row))
        out.append(dict(zip(actual_headers, padded)))
    return out


def _append_row(sheet_id: str, token: str, tab: str, row: list[object]) -> None:
    """Append one row with INSERT_ROWS — the only write primitive proven safe
    under concurrency (12/12 survive vs 3/12 for naive OVERWRITE append)."""
    url = (
        f"{SHEETS_API}/{sheet_id}/values/{quote(tab)}:append"
        "?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )
    _sheet_request("POST", url, token, {"values": [row]})


def _update_row(sheet_id: str, token: str, tab: str, row_number: int, row: list[object]) -> None:
    rng = quote(f"{tab}!A{row_number}")
    url = f"{SHEETS_API}/{sheet_id}/values/{rng}?valueInputOption=RAW"
    _sheet_request("PUT", url, token, {"values": [row]})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_client_history(company_name: str, website: str = "") -> dict | None:
    """Return the matched clients row + last N engagements, or None.

    None means "no history available" for any reason — unset config, no
    match, or an unreachable Sheet (logged, never raised).
    """
    sheet_id = memory_sheet_id()
    if not sheet_id or not company_name.strip():
        return None
    token = _access_token()
    if token is None:
        return None
    try:
        target = client_id_for(company_name, website)
        name_only = client_id_for(company_name)
        clients = _rows_as_dicts(
            _read_tab(sheet_id, token, CLIENTS_TAB), CLIENTS_HEADERS
        )
        # Exact composite match first; fall back to name-only so an intake
        # missing the website still recalls the client.
        client = next((c for c in clients if c.get("client_id") == target), None)
        if client is None:
            client = next(
                (
                    c
                    for c in clients
                    if c.get("client_id", "").split("|")[0] == name_only
                ),
                None,
            )
        if client is None:
            return None
        engagements = _rows_as_dicts(
            _read_tab(sheet_id, token, ENGAGEMENTS_TAB), ENGAGEMENTS_HEADERS
        )
        history = [
            e for e in engagements if e.get("client_id") == client.get("client_id")
        ]
        return {"client": client, "engagements": history[-HISTORY_LIMIT:]}
    except Exception as error:  # noqa: BLE001 - memory must never break intake
        logger.warning("client memory read failed, proceeding without history: %s", error)
        return None


def record_engagement(
    intake: ClientIntake, score: OpportunityScore, source: str, documents: list[str] | None = None
) -> None:
    """Persist one intake run: engagements ledger row first, rollup second.

    Never raises. A failure after the ledger write leaves the rollup stale
    but recomputable — the deliberate ordering choice from the spec.
    """
    sheet_id = memory_sheet_id()
    if not sheet_id or not intake.company_name.strip():
        return
    token = _access_token()
    if token is None:
        return
    cid = client_id_for(intake.company_name, intake.website)
    now = _now()
    try:
        _append_row(
            sheet_id,
            token,
            ENGAGEMENTS_TAB,
            [
                uuid.uuid4().hex[:12],
                cid,
                now,
                source,
                score.tier,
                score.total_score,
                score.max_score,
                json.dumps(intake.pain_points),
                json.dumps(intake.goals),
                intake.budget,
                json.dumps(documents or []),
                "pending",  # outcome is set later by the operator in the Sheet
                "",
            ],
        )
    except Exception as error:  # noqa: BLE001 - memory must never break intake
        logger.warning("client memory write failed, engagement not recorded: %s", error)
        return
    try:
        _upsert_client_rollup(sheet_id, token, cid, intake, score, now)
    except Exception as error:  # noqa: BLE001 - ledger row already durable
        logger.warning("client rollup update failed (ledger row intact): %s", error)


def _upsert_client_rollup(
    sheet_id: str,
    token: str,
    cid: str,
    intake: ClientIntake,
    score: OpportunityScore,
    now: str,
) -> None:
    rows = _read_tab(sheet_id, token, CLIENTS_TAB)
    existing = _rows_as_dicts(rows, CLIENTS_HEADERS)
    for index, client in enumerate(existing):
        if client.get("client_id") == cid:
            count = int(client.get("engagement_count") or 0) + 1
            _update_row(
                sheet_id,
                token,
                CLIENTS_TAB,
                index + 2,  # +1 header row, +1 one-based
                [
                    cid,
                    intake.company_name,
                    intake.website,
                    intake.industry,
                    client.get("first_seen_at") or now,
                    now,
                    count,
                    score.tier,
                    score.total_score,
                    client.get("status") or "prospect",
                    now,
                ],
            )
            return
    _append_row(
        sheet_id,
        token,
        CLIENTS_TAB,
        [
            cid,
            intake.company_name,
            intake.website,
            intake.industry,
            now,
            now,
            1,
            score.tier,
            score.total_score,
            "prospect",
            now,
        ],
    )


def recall_summary(company_name: str, website: str = "") -> dict:
    """Agent-tool-friendly wrapper: always returns a JSON-safe dict."""
    history = read_client_history(company_name, website)
    if history is None:
        return {
            "known_client": False,
            "message": "No prior history for this company (or memory is not configured).",
        }
    return {"known_client": True, **history}
