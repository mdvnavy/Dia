# DIA - Discovery Intake Agent (Gemini ADK)

Gemini/ADK discovery intake agent for the Google for Startups AI Agents Challenge.

DIA turns a discovery questionnaire into a scored opportunity and three
ready-to-use markdown documents:

- `client-profile.md`
- `opportunity-analysis.md`
- `proposal-draft.md`

It ships two complementary paths:

1. **Deterministic pipeline** (`/api/process`) - parses, validates, scores, and
   generates documents with no API key required. Fully reproducible.
2. **Live Gemini agent** (`/api/agent`) - a Google ADK `LlmAgent` that calls the
   Gemini API and uses the deterministic functions as tools. Enabled when a
   `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is configured; degrades gracefully
   otherwise.

## UI features

The demo UI is dependency-free (vanilla JS + built-in browser APIs):

- **Light/dark mode** - sun/moon toggle in the topbar; follows the system
  preference by default and persists the choice.
- **Text-to-speech** - Listen buttons read the generated documents and agent
  replies aloud (Web Speech `speechSynthesis`).
- **Voice responses** - an Auto-speak toggle reads each Gemini reply as it
  arrives; the setting persists across visits.
- **Conversation mode** - hands-free loop (listen, send to the agent, speak
  the reply, listen again) plus one-shot **Dictate** into the Ask-DIA box
  (Web Speech `SpeechRecognition`; controls disable where unsupported).
- **Draft editor** - load any generated document (typically the proposal)
  into a light rich-text editor and polish it into a sendable email: bold,
  italic, underline, bulleted/numbered/lettered lists, visible Undo/Redo
  buttons, and a multifunctional clipboard button (click to copy - with
  formatting where the browser allows - double-click to paste).
- **Share on X** - opens a prefilled x.com post with the score and tier
  summary only (no client details).
- **Accessibility** - keyboard-navigable document tabs (arrow/Home/End),
  aria-live announcements for status and agent output, a skip link, and
  visible focus outlines.

## Run locally

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open `http://127.0.0.1:8080`, click **Load Sample**, run the intake, then use the
**Ask DIA** panel to query the live Gemini agent (requires an API key — see below).

To enable the live agent locally, create a `.env` from `.env.example`:

```bash
cp .env.example .env
# add your key from https://aistudio.google.com/app/apikey
```

On Windows PowerShell you can use the helper `./set-gemini-env.ps1`.

## Deploy to Google Cloud Run

The repo includes a `Dockerfile` and `deploy-cloudrun.sh`. With the gcloud CLI
authenticated:

```bash
PROJECT_ID=your-project GEMINI_API_KEY=your-key ./deploy-cloudrun.sh
```

This enables the required services and runs `gcloud run deploy --source .`,
building the container with Cloud Build and deploying it. The app binds to
`0.0.0.0` and honours Cloud Run's injected `PORT`, and exposes `/healthz` for
health checks. The script prints the public service URL when finished.

To enable Google-managed MCP tools in Cloud Run, pass `GCP_MCP_URL` to the same
script, for example:

```bash
PROJECT_ID=your-project GEMINI_API_KEY=your-key \
  GCP_MCP_URL=https://monitoring.googleapis.com/mcp ./deploy-cloudrun.sh
```

The MCP URL is configuration, not a secret. Authentication uses the Cloud Run
runtime service account through Application Default Credentials, so grant that
service account only the Google Cloud roles the selected MCP endpoint needs.

## Client memory (optional, Sheets-backed)

DIA can remember clients between intakes using a Google Sheet **in your own
Google account** — client data never leaves your Drive and never touches any
shared infrastructure. Design: `docs/2026-07-07_client-memory-layer_design.md`.

Setup:

1. Create a Google Sheet with two tabs named `clients` and `engagements`.
   Put these header rows in row 1:
   - `clients`: `client_id | company_name | website | industry | first_seen_at | last_seen_at | engagement_count | last_tier | last_total_score | status | updated_at`
   - `engagements`: `engagement_id | client_id | timestamp | source | tier | total_score | max_score | pain_points | goals | budget | documents_generated | proposal_outcome | notes`
2. Set `MEMORY_SHEET_ID` to the Sheet's ID (the long segment of its URL).
3. Auth is ADC: locally, `gcloud auth application-default login`; on Cloud
   Run, share the Sheet (Editor) with the runtime service account's email.

Behavior: every scored intake appends an `engagements` row (INSERT_ROWS —
safe under concurrent writers) and updates the client's rollup row. The live
agent gains a `recall_client_history` tool and references prior engagements
when a repeat company comes in. `proposal_outcome` and `status` are yours to
edit directly in the Sheet (e.g. mark a proposal `won`) — human-in-the-loop
memory editing is a feature of the design, not a workaround.

Unset `MEMORY_SHEET_ID` and DIA behaves exactly as before — memory is an
enhancement, never a dependency; an unreachable Sheet degrades to a logged
warning and never blocks or delays an intake.

## Run in Codespaces

This repo includes a `.devcontainer/devcontainer.json` for GitHub Codespaces
(Python 3.12 + Google Cloud CLI). Add `GEMINI_API_KEY` as a Codespaces secret
before running Gemini-backed flows. Do not commit `.env`.

```bash
gcloud --version
python -m pytest -q
python app.py   # forwarded port 8080
```

## Test

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -c "import character; print(character.root_agent.name)"
```

## Project shape

- `client_discovery/core.py` - deterministic parser, validation, scoring, and document generation.
- `client_discovery/models.py` - typed dataclasses for intake, issues, and score.
- `character.py` - ADK `LlmAgent` and custom tools for Gemini-backed execution.
- `agent_runtime.py` - synchronous bridge that runs a single ADK agent turn.
- `app.py` - stdlib HTTP demo shell with deterministic and live-agent endpoints.
- `samples/` - runtime sample questionnaire (ships in the container image).
- `tests/fixtures/` - fictional demo data only.

## HTTP endpoints

| Method | Path                  | Purpose                                   |
| ------ | --------------------- | ----------------------------------------- |
| GET    | `/`                   | Web demo UI                               |
| GET    | `/healthz`            | Cloud Run health check                    |
| GET    | `/api/sample`         | Returns the sample questionnaire markdown |
| GET    | `/api/agent/status`   | Reports whether a Gemini key is configured|
| POST   | `/api/process`        | Deterministic parse/score/generate        |
| POST   | `/api/agent`          | Live Gemini ADK agent turn                |

## Submission hygiene

Do not commit `.env`, real client data, support tickets, private prompts, or
unrelated internal product material. Use only fictional fixtures in public
demos and repositories. See [SECURITY.md](SECURITY.md) for secret handling,
CI secret scanning, and Cloud Run Secret Manager usage.
