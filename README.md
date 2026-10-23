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
- **Tia-style shell** - centred wordmark, pill composer with a round send
  button, slim top bar with a wordmark menu, and a "Learn how to use DIA"
  dialog. Ask DIA and the results appear below the composer after a run.
- **Command palette** - Ctrl/Cmd+K opens it; type to filter, arrow keys to
  move, Enter to run, Esc to close. Press `/` to jump to the input.
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
| GET    | `/api/agent/status`   | Gemini key configured? + agent auth mode  |
| POST   | `/api/process`        | Deterministic parse/score/generate        |
| POST   | `/api/agent`          | Live Gemini ADK agent turn                |

## Live agent access

Interim policy until DIA has proper accounts (`agent_auth.py`, `agent_quota.py`):

| Who | Ask DIA (`/api/agent`) |
| --- | ---------------------- |
| Visitors | Open, no sign-in. **10 questions per visitor per calendar day (America/New_York)**, then a friendly 429: "You've used today's 10 questions. Come back tomorrow!" |
| Signed in with Google, email in `OWNER_EMAILS` | Unlimited, and skips the burst guard. |
| Signed in with any other Google account | Same as a visitor (10/day). |

**Google Sign-In.** The *Sign in* button sits in the top bar's right corner,
just before the theme toggle. It uses Google Identity Services (the same
pattern as Tia): the browser gets a Google ID token and POSTs it once to
`/api/auth/session`, where the server verifies it with `google-auth`
(signature, `aud` = `GOOGLE_CLIENT_ID`, `iss` = `accounts.google.com`, `exp`,
`email_verified`). It then sets a `dia_session` cookie that lasts 12 hours. The
cookie is HttpOnly, SameSite=Lax, Secure on Cloud Run, and HMAC-signed with
`DIA_SESSION_SECRET`. It carries only the verified email, and owner status is
re-checked against `OWNER_EMAILS` on every request. *Sign out* clears the
cookie and calls `google.accounts.id.disableAutoSelect()`. The ID token is
never stored in the page or logged.

Without `GOOGLE_CLIENT_ID` or a `DIA_SESSION_SECRET` of at least 32
characters, the button is hidden and everyone gets the visitor cap. Rotating
`DIA_SESSION_SECRET` signs everyone out.

**Who is a "visitor"?** Each question is charged to two buckets and allowed only
if both are under the limit: the client IP (on Cloud Run the right-most
`X-Forwarded-For` hop, which Google appends; the left-most hops are
client-supplied and ignored; IPv6 is collapsed to its /64) and a random
`dia_vid` cookie (HttpOnly, SameSite=Lax, Secure on Cloud Run). Clearing
cookies doesn't reset the cap (the IP bucket is full), and nor does switching
networks with the same browser (the cookie bucket is). Keys are stored as
salted HMAC-SHA256 hashes (`DIA_VISITOR_SALT`), never raw IPs. People sharing
one IP (an office NAT) share its 10 questions.

**Where counts live.** `DIA_QUOTA_STORE=firestore` keeps counters in
Firestore (one atomic `commit` with an increment transform per question;
documents in `dia_agent_quota` carry `expire_at` for a TTL policy), so the cap
holds across Cloud Run instances. Without it the counters are **in memory, per
instance, and lost when an instance restarts**: a visitor can get up to
`10 x instances` questions a day, plus a fresh 10 whenever Cloud Run recycles
an instance. If you run without Firestore, keep `--max-instances` at 1–2.

**If Firestore is down** the question is counted in the in-memory store
instead (fail-soft): visitors aren't locked out by an outage, and spend is
still capped (per instance), never unlimited.

Only questions that actually reach Gemini are charged: empty messages and
"agent not configured" replies are free. A short per-IP burst guard
(`DIA_AGENT_RATE_LIMIT`, default `10/60`, in memory) also stays in front, so
scripted clients are throttled before touching the quota store. Owners bypass
both. Sign-in attempts have their own burst guard.

**Kill switch.** `ASK_DIA_ENABLED=false` turns Ask DIA off for everyone,
owners included, without a redeploy of code (an env change on the service is
enough). `/api/agent` then answers **503** with `Retry-After: 300` and a calm
"Ask DIA is temporarily unavailable" message before reading the body, touching
the quota store or calling Gemini; `/api/agent/status` reports
`"enabled": false`, and the UI shows the notice and disables the Ask DIA
composer while the intake tools keep working. Unset or empty means enabled; an
unrecognised value (a typo) is treated as **off**.

MCP toolsets are attached only via `DIA_AGENT_MCP_MODE` (`none` by default, or
`make`, `gcp`, `all`); words in a prompt never switch tools on. The
deterministic `/api/process` path is unaffected by all of this.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `ASK_DIA_ENABLED` | enabled | Kill switch: `false`/`0`/`off`/`no` (or any unrecognised value) returns 503 "temporarily unavailable". |
| `GOOGLE_CLIENT_ID` | unset | OAuth Web client ID for Google Sign-In (public). Unset = no sign-in button. |
| `OWNER_EMAILS` | unset | Comma list of Google emails with unlimited access (case-insensitive). |
| `DIA_SESSION_SECRET` | unset | Signs the 12h session cookie (≥32 chars, from Secret Manager). Unset = sign-in off. |
| `DIA_AGENT_DAILY_LIMIT` | `10` | Questions per visitor per day (`0` = visitors can't use Ask DIA). |
| `DIA_QUOTA_STORE` | `memory` | `firestore` for a cap shared across instances. |
| `DIA_FIRESTORE_PROJECT` / `DIA_FIRESTORE_DATABASE` / `DIA_FIRESTORE_COLLECTION` | ADC project / `(default)` / `dia_agent_quota` | Firestore location. |
| `DIA_VISITOR_SALT` | built-in | Secret salt for hashing visitor keys (set it in production). |
| `DIA_AGENT_RATE_LIMIT` | `10/60` | Burst guard, calls/seconds per IP (`0` disables). |
| `DIA_TRUST_FORWARDED_FOR` | off | Trust `X-Forwarded-For` off Cloud Run (behind your own proxy). |

## Submission hygiene

Do not commit `.env`, real client data, support tickets, private prompts, or
unrelated internal product material. Use only fictional fixtures in public
demos and repositories. See [SECURITY.md](SECURITY.md) for secret handling,
CI secret scanning, and Cloud Run Secret Manager usage.
