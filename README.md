# DIA - Discovery Intake Agent ADK Demo

Gemini/ADK-ready discovery intake agent for the Google for Startups AI Agents Challenge.

DIA converts a fictional discovery questionnaire into:

- `client-profile.md`
- `opportunity-analysis.md`
- `proposal-draft.md`

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000`, load the sample questionnaire, and run the intake.

## Run in Codespaces

This repo includes a `.devcontainer/devcontainer.json` for GitHub Codespaces. It uses Python 3.12 with the Google Cloud CLI feature and installs `requirements.txt` when the Codespace is created.

1. Create a Codespace from the GitHub repo.
2. Add `GEMINI_API_KEY` as a Codespaces secret before running Gemini-backed agent flows. Do not commit `.env`.
3. Check the SDK and tests:

```bash
gcloud --version
python -m pytest -q
python app.py
```

Open the forwarded port `5000` to use the DIA local demo.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -c "import character; print(character.root_agent.name)"
```

## Project shape

- `client_discovery/core.py` - deterministic parser, validation, scoring, and document generation.
- `client_discovery/models.py` - typed dataclasses for intake, issues, and score.
- `character.py` - ADK `LlmAgent` and custom tools for Gemini-backed execution.
- `app.py` - stdlib HTTP demo shell and deterministic `/api/process` endpoint.
- `tests/fixtures/` - fictional demo data only.

## Submission hygiene

Do not commit `.env`, real client data, support tickets, private prompts, or unrelated internal product material. Use only fictional fixtures in public demos and repositories.
