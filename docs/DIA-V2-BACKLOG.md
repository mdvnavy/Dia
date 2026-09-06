# DIA v2 — Backlog

Captured from a live end-to-end test of the deployed app
(`https://dia-discovery-intake-623133365140.us-central1.run.app/`, 2026-06-14).
Items are ordered by **impact-per-effort** — top items are quick wins, bottom
items are bigger architectural moves.

Track high-value items as GitHub issues on `mdvnavy/Dia`. Use labels
`bug`, `ux`, `perf`, `arch`, `a11y`. This file is the human-readable
narrative; issues are the unit of work.

---

## P0 — Quick wins (≤30 min each)

### BUG-01 · Date hallucination in `/api/agent` response
- **Symptom:** When the user asks "what are the risks", Gemini responded
  with *"June 2026, which is over a year away"* and *"Q2 2026 make June
  the ideal time to launch."* Today is 2026-06-14 — June 2026 is *this
  month*. The model has no temporal anchor.
- **Fix:** Inject `current_date: 2026-06-14` into the `/api/agent`
  request body, or append a single line to the system prompt:
  `Today's date is 2026-06-14. Use it for all relative-time reasoning.`
- **File:** `app.py` (the request body construction for the ADK call).
- **Verify:** Re-ask the same question, confirm the response no longer
  says "over a year away".

### BUG-02 · Button label vs. DOM id mismatch
- **Symptom:** Button text says **"Run Intake"** but `id="runAgent"`.
  The Send-to-Gemini button text is **"Send to Gemini"** but
  `id="runChat"`. Harmless for users, painful for anyone scripting
  Playwright/Kapture/MCP tests.
- **Fix:** Either rename the buttons to match (`Run Agent`, `Send to Chat`),
  or add a `data-testid` that matches the visible text. Don't break
  any existing hooks — keep the old id as `aria-labelledby` if needed.

### A11Y-01 · Static instruction text uses `role="status"`
- **Symptom:** The placeholder line *"The live agent uses the Gemini
  API via Google ADK. Run an intake first, then ask a question."* is
  marked `role="status"`. That's a live region — screen readers will
  announce it on every page load as if a status changed.
- **Fix:** Change to a normal `<p>` or `<small>`, drop the role. (Or
  use `<aside>` if it should be a callout.)

---

## P1 — UX polish

### UX-01 · No streaming / loading state for Gemini chat
- **Symptom:** `/api/agent` takes ~17s for a 3-bullet answer. During
  that window, the `<pre>` block sits at the *previous* response (or
  empty placeholder) with no feedback. Users will think the button
  didn't fire.
- **Fix:** On `runChat` click, immediately replace the `<pre>` content
  with a "Thinking…" placeholder (or a spinner) before the fetch
  resolves. Clear on success/error.
- **Bonus:** Stream the response (Server-Sent Events or chunked
  transfer) so the user sees tokens as they arrive. ADK supports this.

### UX-02 · `/api/agent` latency is 17.4s
- **Symptom:** 17 seconds for ~250 tokens of output is a lot. Either
  cold-start on the ADK agent, or model is too heavy for the question.
- **Investigate:**
  1. Time the ADK agent invocation server-side (log start/end).
  2. Check which Gemini model the agent uses. For short analytical
     questions, `gemini-2.5-flash` is probably the right tier.
  3. If cold-start dominates, consider keeping a warm pool
     (Cloud Run `min-instances=1`).
- **File:** `agent_runtime.py`, `app.py`, cloud-run deploy flags.

### UX-03 · No "Clear conversation" affordance
- **Symptom:** Once Gemini has responded, the only way to clear the
  `<pre>` is to reload the page.
- **Fix:** Add a "Clear" button next to the "Conversation" button.

---

## P2 — Robustness

### ROBUST-01 · Deterministic parser doesn't fall back on messy input
- **Symptom:** `/api/process` parses the questionnaire as a markdown
  table. If a user pastes freeform prose or a non-standard table
  format, the parser likely returns empty fields silently and produces
  a score of 0/20 with no explanation.
- **Fix:** Detect when the parser returns `< N` fields (say, < 4) and
  fall back to a Gemini call: *"Extract the following fields from
  this intake prose, return JSON."* Use the deterministic parser's
  output as ground truth; use Gemini only to fill gaps.

### ROBUST-02 · Score thresholds are baked into code
- **Symptom:** Tier boundaries, urgency/budget/tech weights, and
  max-score values are scattered across `app.py` and
  `client_discovery/`. Changing them requires a redeploy and a
  code review.
- **Fix:** Move thresholds to a single config file
  (`client_discovery/scoring_rules.yaml` or `.json`). Score reasons
  in the API response should reference the rule that fired so the
  logic is auditable.

### ROBUST-03 · MCP install pending
- **Symptom:** `kapture` MCP works on the dev side but isn't wired
  into the live agent's tool list (per project memory, install is
  pending). The natural tools are:
  - `intake_risk_analysis`
  - `intake_proposal_review`
  - `intake_score_audit`
- **Fix:** Define the MCP server config, add to the ADK agent's
  tool list, redeploy. Pieces LTM is the natural backing store
  for prior client outcomes — pipe them into the prompt as
  *"similar past engagements"*.

---

## P3 — Architecture

### ARCH-01 · Two-tier split is sharp — keep it
- **Note:** The current `/api/process` (deterministic, 81ms, no
  LLM tokens) + `/api/agent` (Gemini ADK, 17s, LLM tokens) split
  is the right pattern. Don't merge them. Don't move the table
  parser into Gemini. Cheap deterministic work for structured
  output, expensive LLM only for open-ended analysis.

### ARCH-02 · Observability wiring
- **Symptom:** GFE returns `x-cloud-trace-context` headers, but I
  didn't see structured request logs with timings inside the
  agent handler. Cold-start vs. model-latency breakdown is
  invisible.
- **Fix:** Add a small middleware (or per-route decorator) that
  logs `{route, status, duration_ms, tokens_in, tokens_out,
  model}`. Cloud Run + Cloud Logging will pick it up. One hour
  of work, huge payoff for the next round of perf tuning.

### ARCH-03 · Deploy script duplication
- **Symptom:** `deploy.sh`, `deploy.ps1`, `deploy-cloudrun.sh`
  in repo root. Probably three slightly-different versions of
  the same gcloud command. Pick one, delete the others, or
  have all three call a single `deploy.groovy`/Makefile.
- **Why it matters:** When `cloud-run.yaml` changes, you have
  to remember to update all three. They will drift.

---

## Test artifacts (for the record)

End-to-end run on 2026-06-14 against the live Cloud Run deploy:

| Step | Endpoint | Latency | Notes |
| --- | --- | --- | --- |
| Load Sample | `GET /api/sample` | 1368 ms | 1356 B response, Northstar Studio fixture |
| Run Intake | `POST /api/process` | 81 ms | 3793 B response, tier=Custom AI Agent, 12/20 |
| Send to Gemini | `POST /api/agent` | 17,424 ms | 2490 B response, 3 risks + follow-up questions |

Sample question: *"List the top 3 risks for this engagement and what
additional intake questions would close them."* — Gemini answered
with a budget-mismatch risk, a timeline risk (with the date bug
described above), and a demo-expectation risk.

---

## How to use this file

1. Pick the next item off the top.
2. Open a GitHub issue (`gh issue create` on `mdvnavy/Dia`) with the
   bug id, link to this section, and `Fixes #N` in the PR.
3. Don't bundle P0 quick wins with P3 architecture work — separate
   PRs, separate reviews.
4. When an item ships, strike it from this file in the same PR.
