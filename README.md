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
demos and repositories.
