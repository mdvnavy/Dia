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
   Gemini on **Vertex AI** and uses the deterministic functions as tools.
   Enabled when `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT` and
   `GOOGLE_CLOUD_LOCATION` are set (auth is ADC); degrades gracefully
   otherwise. There is no API-key path: `GEMINI_API_KEY` / `GOOGLE_API_KEY`
   are ignored even when present (`vertex_config.py`).

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
PROJECT_ID=your-project ./deploy-cloudrun.sh   # optional: VERTEX_LOCATION=global (default)
```

The runtime service account needs `roles/aiplatform.user` (the script does not
grant IAM). The script sets the three Vertex env vars with `--update-env-vars`
(other env on the service is kept) and unbinds any old `GEMINI_API_KEY` secret.

This enables the required services and runs `gcloud run deploy --source .`,
building the container with Cloud Build and deploying it. The app binds to
`0.0.0.0` and honours Cloud Run's injected `PORT`, and exposes `/healthz` for
health checks. The script prints the public service URL when finished.

To enable Google-managed MCP tools in Cloud Run, pass `GCP_MCP_URL` to the same
script, for example:

```bash
PROJECT_ID=your-project \
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
(Python 3.12 + Google Cloud CLI). Gemini-backed flows use Vertex AI: run
`gcloud auth application-default login` and set the three Vertex env vars
from `.env.example`. Do not commit `.env`.

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

## Access and usage limits (lockdown v2)

**No use without signing in, same as Tia.** Signed out, anyone can read and
browse: the landing page, How it works, the business card link, static
assets, `/api/config` and health checks. Every functional route needs a
verified Google sign-in and otherwise answers **401
`{"error": "sign_in_required"}`** before reading the body or doing any work.
The UI shows a calm "Sign in with Google" prompt where the composer and Run
would be.

| Route | Signed out | Signed in (free) | Charged |
| ----- | ---------- | ---------------- | ------- |
| `GET /`, static files, `/healthz`, `/health`, `/api/config`, `/api/auth/config`, `/api/agent/status` | 200 | 200 | no |
| `POST /api/auth/session`, `POST /api/auth/logout` | public (IP-hash burst guard) | — | no |
| `POST /api/process` | 401 | 10 generations/day | 1 generation |
| `POST /api/pdf` with `{questionnaire}` | 401 | counts as a generation | 1 generation |
| `POST /api/pdf` with `{result}` (re-render a saved run) | 401 | export | no |
| `POST /api/agent` | 503 if `ASK_DIA_ENABLED` off, else 401 | 5 Ask DIA/day | 1 Ask DIA |
| `POST /api/extract`, `GET /api/sample` | 401 | allowed | no |
| `GET /api/usage` | 401 | the caller's counts | no |

Routes live in one table in `app.py` (`GET_ROUTES` / `POST_ROUTES`), and
`tests/test_sign_in_gate.py` fails if a route is added outside it or ungated
without being on the public allow-list.

**Owners.** `OWNER_EMAILS` is a comma list. A user is an owner only when the
verified ID token's `email` (with `email_verified: true`) matches an entry,
trimmed and case-insensitive. Owners are uncapped and skip the burst guards.
Nothing the client sends (body fields, headers, other cookies) can make
someone an owner.

**Sign-in.** Google Identity Services gives the browser an ID token, which is
POSTed once to `/api/auth/session`. The server verifies it with `google-auth`
(signature, `aud` = `GOOGLE_CLIENT_ID`, `iss`, `exp`, `email_verified`) and
sets `dia_session`: HttpOnly, SameSite=Lax, Secure on Cloud Run, HMAC-signed
with `DIA_SESSION_SECRET`, bound to the token's `sub` and the client ID, and
valid for 2 hours by default. Tampered, expired or foreign cookies are
refused. Replaying the same Google token just mints another cookie for the
same `sub`, so it never adds quota.

**Counting.** Counts are keyed on an HMAC of the verified `sub`
(`DIA_VISITOR_SALT`), never on IPs, cookies or anything client-side. A unit is
charged atomically **before** the work and refunded on a server error. The
day resets at midnight America/Toronto. At the cap the server answers **429**
`{"error": "daily_limit", "bucket", "message", "usage"}` with `Retry-After`;
the message is calm ("You've used today's 5 Ask DIA questions. More at
midnight ET.") with no price and no urgency. A per-user burst guard (3 per 10
seconds) sits in front.

**Where counts live.** `DIA_QUOTA_STORE=firestore` keeps counters in a
Firestore **Native-mode** database (`DIA_FIRESTORE_DATABASE`, planned
`dia-quota`, because the project's `(default)` database is Datastore mode).
Each charge is a read-write transaction (`beginTransaction` → read →
`commit`), retried with jittered backoff on `ABORTED`, so parallel requests
can't overspend. Documents carry `expire_at`; add a TTL policy on that field.
Without Firestore, counters are in memory per instance. If Firestore is down,
Ask DIA fails closed (503 `quota_unavailable`) and generations fall back to
the per-instance memory store.

**Client IP.** Only the sign-in burst guard uses an IP, as a salted hash. On
Cloud Run the **right-most** `X-Forwarded-For` entry is used: Google's front
end appends the address it saw, and everything to its left is client-supplied
and forgeable. If the service is ever put behind an external load balancer,
revisit this (the right-most entry would be the balancer).

**Kill switch.** Ask DIA is **off unless `ASK_DIA_ENABLED` is exactly
`1`/`true`/`yes`/`on`**. Off, `/api/agent` answers **503** with
`Retry-After: 3600` before sign-in, the body, the quota store or Gemini, for
owners too. The intake tools keep working.

**Usage counter.** Signed in, the top bar shows `GENERATIONS 7/10 · ASK DIA
3/5` (owners see `OWNER · UNLIMITED`). It is display-only: it is filled from
`GET /api/usage` and from the `usage` field of each run's response.

MCP toolsets are attached only via `DIA_AGENT_MCP_MODE` (`none` by default, or
`make`, `gcp`, `all`); words in a prompt never switch tools on.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `ASK_DIA_ENABLED` | off | Kill switch: only `1`/`true`/`yes`/`on` enable Ask DIA. |
| `GOOGLE_CLIENT_ID` | unset | OAuth Web client ID (public). Unset = nobody can sign in. |
| `DIA_SESSION_SECRET` | unset | Signs session cookies (≥32 chars, from Secret Manager). |
| `DIA_SESSION_TTL_SECONDS` | `7200` | Session lifetime (clamped to 5 min–12 h). |
| `OWNER_EMAILS` | unset | Comma list of owner emails (verified email only). |
| `DIA_GENERATION_DAILY_LIMIT` | `10` | Generations per user per day. |
| `DIA_ASK_DAILY_LIMIT` | `5` | Ask DIA questions per user per day. |
| `DIA_QUOTA_STORE` | `memory` | `firestore` for counts shared across instances. |
| `DIA_FIRESTORE_PROJECT` / `DIA_FIRESTORE_DATABASE` / `DIA_FIRESTORE_COLLECTION` | ADC project / `(default)` / `dia_agent_quota` | Firestore location (use a Native-mode DB, e.g. `dia-quota`). |
| `DIA_VISITOR_SALT` | built-in | Secret salt for hashing user ids and IPs (set it in production). |
| `DIA_BURST_LIMIT` | `3/10` | Per-user burst guard for generations and Ask DIA. |
| `DIA_UTILITY_BURST_LIMIT` | `10/10` | Per-user burst guard for extract and PDF export. |
| `DIA_AUTH_RATE_LIMIT` | `10/60` | Sign-in attempts per salted IP hash. |
| `DIA_TRUST_FORWARDED_FOR` | off | Trust `X-Forwarded-For` off Cloud Run (behind your own proxy). |
| `DIA_LOCAL_DEV_NO_SIGN_IN` | off | **Local only.** Treat sessionless requests as an uncapped developer. Ignored on Cloud Run; never set it on a public deployment. |

### Sign-in: same scheme as Tia

Token verification is ported from Tia (`mdvnavy/tia-sia`, `packages/auth/tokens.py`
`GoogleTokenVerifier`): `google.oauth2.id_token.verify_oauth2_token` checks the
RS256 signature against Google's published keys, `aud` = `GOOGLE_CLIENT_ID` and
`exp`; then `iss` must be `accounts.google.com`, and `email`, `email_verified=true`
and `sub` must all be present. The same error classes are used (expired, audience,
issuer, tampered, unverified). The frontend wiring matches Tia: the GIS script,
`initialize({client_id, callback})`, the outline/large/"Sign in with"/pill button at
fitted width, and the credential sent as `Authorization: Bearer <id token>` to
`POST /api/auth/session`. The response carries Tia's `status`, `authorized`,
`message` and `user` (sub, email, name), plus DIA's `signed_in`, `email` and `owner`.

Deliberate differences:

- **Cookie session instead of a Bearer token on every call.** Tia keeps the ID token
  in `sessionStorage` and verifies it on each request. DIA swaps it once for a signed
  HttpOnly `dia_session` cookie (HMAC, bound to `sub`, `aud` and `email_verified`,
  2 h by default). That way scripts on the page can't read it, a raw ID token can't
  unlock functional routes, and quota stays keyed on `sub`.
- **10 s clock skew** (Tia: 0), so a fresh token isn't rejected over a small clock drift.
- **No invites, profiles or waitlist.** Every verified Google account gets the free
  tier, and `OWNER_EMAILS` (verified email only) is uncapped.
- **Fails closed.** A missing `GOOGLE_CLIENT_ID` or a missing or short
  `DIA_SESSION_SECRET` disables sign-in (404). Every functional route then returns
  401, including requests carrying a previously valid cookie.

`tests/test_sign_in_flow.py` exercises the real verifier with a locally generated RSA
key. A stub transport serves its public key at Google's certs URL, in both x509 and
JWKS form, so no network is used.

## Submission hygiene

Do not commit `.env`, real client data, support tickets, private prompts, or
unrelated internal product material. Use only fictional fixtures in public
demos and repositories. See [SECURITY.md](SECURITY.md) for secret handling,
CI secret scanning, and Cloud Run Secret Manager usage.
