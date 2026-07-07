# Design: Client Memory Layer (Sheets-Backed)

## Date: 2026-07-07

## Context

DIA currently treats every intake as stateless: one questionnaire in, three
documents out, no memory of whether this company has been seen before, what
was discussed last time, or whether a prior proposal was sent, won, or lost.

This session proved out a reusable pattern for exactly this kind of state —
`sheets-mini-db` (see `agent-eggs/sheets-mini-db/PROOF.md`): a Google Sheet
used as a live, multi-writer datastore. Proven live: naive `append`
(OVERWRITE mode) silently loses concurrent writes (3/12 survived in a
12-parallel-writer test); `append --insert INSERT_ROWS`, or routing through
an Apps Script `LockService` web app, is safe (12/12, zero loss).

This spec defines a client-memory layer for DIA built on that pattern. It is
a **specification only** — no implementation. Building it is deliberately
deferred to a later session.

## Non-goals (explicitly out of scope here)

- Any code changes.
- The gog action loop (Gmail/Drive/Sheets automation replacing the Make
  scenarios) — separate spec.
- Stripe / revenue tracking — separate spec.
- Splitting DIA into multiple agents (research agent, proposal agent) —
  not required for memory and out of scope.
- NotebookLM-grade RAG grounding over uploaded client documents — later,
  separate, and a different mechanism (Vertex AI RAG / Gemini File Search)
  from the structured memory described here.

## Design principles

- **Client data never touches the HF skill registry.** That registry
  (`45Navy/agent-skills`) is for portable *capabilities* (skills, code,
  memory-pods about the *project itself*). A client's pain points, budget,
  and proposal history are business data and belong in the operator's own
  Google Sheet/Drive — never mixed into shared infrastructure other agents
  or sessions read from. This is the same boundary rule this session used
  for the `gog` skill registry vs. the sheets-mini-db proof.
- **Single-tenant for v1.** One Sheet per DIA deployment (i.e., per agency
  running it), not a shared multi-client-of-DIA sheet. Multi-tenant (each
  reseller customer with their own Sheet/OAuth) is a bigger redesign and
  explicitly deferred.
- **Degrades gracefully, same posture as the Gemini key.** `app.py` already
  runs the deterministic pipeline with no API key and only enables the live
  agent when one is configured, logging a warning otherwise. Memory must
  follow the identical shape: unset → DIA behaves exactly as it does today,
  zero errors, zero behavior change. It is an enhancement, never a hard
  dependency.
- **Writes use the proven-safe primitive.** `INSERT_ROWS` (or the Apps
  Script `LockService` web app) only. Naive `append` is disqualified by the
  proof — it silently drops data under concurrency.

## Data model

Two tables, both a persistence view of dataclasses that already exist in
`client_discovery/models.py` — no new fields are invented, only persisted.

**`clients`** — one row per known company, identity + rollup:

| column | source |
|---|---|
| `client_id` | derived (see Open Questions #1) |
| `company_name` | `ClientIntake.company_name` |
| `website` | `ClientIntake.website` |
| `industry` | `ClientIntake.industry` |
| `first_seen_at` | set on first write |
| `last_seen_at` | updated every write |
| `engagement_count` | incremented every write |
| `last_tier` | `OpportunityScore.tier` |
| `last_total_score` | `OpportunityScore.total_score` |
| `status` | prospect / proposal_sent / won / lost / dormant |
| `updated_at` | every write |

**`engagements`** — one row per intake run, append-only ledger:

| column | source |
|---|---|
| `engagement_id` | derived |
| `client_id` | FK to `clients` row |
| `timestamp` | write time |
| `source` | which endpoint ran it: `/api/process` or `/api/agent` |
| `tier`, `total_score`, `max_score` | `OpportunityScore` |
| `pain_points`, `goals` | `ClientIntake` (serialized) |
| `budget` | `ClientIntake.budget` |
| `documents_generated` | which of the 3 docs were produced |
| `proposal_outcome` | pending / sent / won / lost / n-a — **not** set at write time (see Write path) |
| `notes` | free text |

## Read path — when DIA recalls memory

- **Trigger:** a new intake's `company_name` (normalized) matches an
  existing `clients` row.
- **What's pulled:** the matched `clients` row + the last N `engagements`
  rows (cap at ~3 to bound context size and read volume).
- **Where it plugs in:**
  - Deterministic path (`client_discovery.core.generate_documents`) —
    future enhancement to let the proposal draft reference prior
    engagement context. Not part of this spec's minimum scope.
  - Agent path (`character.py`) — a new tool, e.g.
    `recall_client_history(company_name)`, added to the `tools` list built
    in `build_agent()`. This follows the **exact existing pattern** of
    `_make_mcp_toolset()` / `_gcp_mcp_toolset()`: gated by an env var
    (e.g. `MEMORY_SHEET_ID`), returns a no-op/absent tool when unset, so
    keyless and local test runs are unaffected. The LLM decides when to
    call it.
- **Latency budget:** Sheets reads run ~0.6–1.4s per the proof. Acceptable
  for a per-intake read (not a hot path), but must time out and fall back
  to "no history available" rather than block document generation.

## Write path — when DIA records memory

- **Trigger:** after a `/api/process` or `/api/agent` run completes
  scoring successfully (see Open Questions #3 for the validation-failure
  case).
- **What's written:** one new `engagements` row (INSERT_ROWS-safe append),
  then an update to the matching (or newly created) `clients` row's
  rollup fields.
- **Ordering matters, and is a deliberate design choice:** the two writes
  are not atomic against each other — Sheets has no cross-row
  transactions (an honest limit from the proof). Write the `engagements`
  row **first** (durable, append-only, safe even if the next write fails),
  then update the `clients` rollup. If the rollup update fails, history is
  still intact and the rollup is recomputable later; nothing is lost,
  only briefly stale.
- **`proposal_outcome` is intentionally not set at write time.** DIA has
  no way to know at intake time whether a proposal will be sent, won, or
  lost. This field is updated later — either by the agency owner editing
  the cell directly in the Sheet, or by a future automation hook (e.g. a
  `gog-gmail` reply-detection step, out of scope here). This is the
  "human-in-the-loop memory editing" property that any Sheets-backed store
  gets for free, and it should be treated as a designed-in feature, not a
  gap to fix later.

## Where this lives architecturally

- **New module (future):** `client_discovery/memory.py`. `core.py` today
  has zero external I/O — it's pure parsing/scoring/templating. Memory
  read/write against the Sheets API is a distinct concern and should not
  be folded into `core.py`; a separate module preserves that purity, the
  same way `agent_runtime.py` is kept separate from `app.py`.
- **Config:** one new env var, `MEMORY_SHEET_ID`, following the exact
  present-means-on / absent-means-off pattern already used by
  `MAKE_MCP_URL`, `GCP_MCP_URL`, and `GEMINI_API_KEY`.
- **Agent tool surface:** `character.py` gains a memory toolset (plain
  Python functions, e.g. `recall_client_history`, optionally
  `record_engagement_outcome` for explicit "mark this proposal won" during
  a chat), added in `build_agent()` alongside the existing
  `include_make_mcp` / `include_gcp_mcp` flags — e.g. `include_memory`,
  auto-detected from `MEMORY_SHEET_ID` presence.
- **HTTP surface (optional, not required for the core loop):** a
  read-only endpoint such as `GET /api/client/{id}/history` so the UI can
  show "we've seen this client before."

## Failure modes / degradation

| condition | behavior |
|---|---|
| `MEMORY_SHEET_ID` unset | identical to today's behavior, no errors |
| Sheet unreachable at read time | log a warning, proceed with no history — same posture as the GCP MCP toolset's `except Exception` → disabled-with-warning |
| Sheet unreachable at write time | see Open Questions #4 — not yet decided |
| concurrent writes from multiple intakes | `INSERT_ROWS` handles this per the proof (12/12 survive); no special handling needed |

## What this buys for the XPRIZE pitch

- **"AI-Native Operations"** — memory demonstrates the AI tracking actual
  business state (client history, deal status), a concrete answer to
  "does AI execute key decisions," not a stateless form-filler.
- **"Business Viability"** — the `engagements` ledger is a machine-countable
  record of real client engagements run through DIA over the judging
  window, not a claim.
- **Trust story for small agencies** — "your client history lives in a
  spreadsheet in your own Drive, not our database" directly answers the
  two standard objections to adopting an AI tool: data lock-in and vendor
  disappearance.

## Open questions (unresolved — decide before implementation)

1. **Identity resolution.** How a new intake's `company_name` is matched
   to an existing `clients` row. Candidates: normalized name + website
   domain as a composite key (simplest, no new dependency); human-in-the-
   loop confirmation on ambiguous matches; accept some early duplication
   and clean up later. Not resolved here.
2. **Auth mechanism** for Sheets API access from the Cloud Run deployment
   — service account vs. OAuth vs. reusing `gog`'s stored credentials. The
   README's existing ADC pattern for GCP-managed MCP tools is a likely
   reusable precedent, but Sheets API scope grants need confirming.
3. **Do failed/invalid intakes get an `engagements` row?** Probably not —
   likely only engagements that reach a scored state should be recorded —
   but this should be an explicit decision, not a default.
4. **Write reliability tier for v1.** Accept data loss on a Sheet outage
   at write time (simpler, matches this repo's ship-then-iterate posture)
   vs. a local retry queue (more robust, more complexity). Not resolved.
5. **Single-tenant vs. multi-tenant.** V1 assumes one Sheet per DIA
   deployment. If DIA is ever resold to multiple agencies, each needs
   their own Sheet and credentials — a materially bigger redesign,
   explicitly out of scope for this spec.
6. **Plain tool vs. MCP for `recall_client_history`.** A plain Python tool
   is simpler and adds no infrastructure dependency, matching
   `parse_intake` et al. Routing through MCP would make sense only if a
   shared "memory MCP server" is later built for reuse across other agents
   — plausible given this session's broader direction, but not justified
   for DIA alone. Recommend the plain-tool approach; flag MCP as the
   future alternative.
