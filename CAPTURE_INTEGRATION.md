# Capture side — integration notes

This branch (`claude/capture-side`) is based on `claude/hopeful-meitner-cruh8w` and adds
the **OBS capture side** *additively*. None of the twin's files (`app.py`, `character.py`,
`agent_runtime.py`, `Dockerfile`, deploy, static, templates) were modified. Drop-in only.

## What this branch adds

| File | Change | Notes |
|------|--------|-------|
| `client_discovery/core.py` | +283 | Adds `trigger_obs_screenshot()`, `save_obs_replay_buffer()`, `refine_with_jules()`, `_resolve_obs_credentials()`. `generate_documents()` gains an **optional** 3rd arg `strategic_analysis=None` — existing 2-arg calls still work. |
| `client_discovery/service.py` | new | `build_intake_response()` + `read_sample_questionnaire()`. Bundles intake → (optional) Gemini agent → (optional) Jules → OBS capture. See overlap note below. |
| `client_discovery/config.py` | new | `load_config()` — reads env (incl. OBS creds). Raises `ValueError` if `GEMINI_API_KEY` missing and `TESTING != "true"`. |
| `requirements.txt` | +3 | `requests`, `obsws-python`, `obswebsocket` (capture deps; superset of yours). |
| `tests/test_core.py`, `test_config.py`, `test_adversarial_coverage.py`, `conftest.py` | new/updated | Capture + core logic coverage. |

## Verified

```
pytest tests/test_core.py tests/test_config.py tests/test_adversarial_coverage.py
26 passed in 0.31s
```
`app.py` + `character.py` + all capture modules import together; signatures compatible.

## The one wiring step (in `app.py`, your call)

The capture trigger is not yet wired into your inline `build_intake_response`, because that's
where our two `app.py` rewrites collide and you own integration. To enable capture, after a
successful intake (in your `handle_process` / `build_intake_response`) add:

```python
from client_discovery.core import trigger_obs_screenshot, save_obs_replay_buffer

# after documents are generated, when issues were found:
if issues:
    try:
        trigger_obs_screenshot()
        save_obs_replay_buffer()
    except Exception as e:  # never let capture break the response
        logger.warning(f"Programmatic OBS triggering failed: {e}")
```

OBS connection is resolved from env via `_resolve_obs_credentials()` (host/port/password).
No OBS running → it logs a warning and the response is unaffected.

## Overlap to resolve

- **`service.py` vs your `agent_runtime.py`** — both call the Gemini agent. `service.build_intake_response`
  does intake+agent+Jules+capture inline; you wired a separate `/api/agent` endpoint via `agent_runtime`.
  Pick **one** path; don't run both. `service.py` is included as a reference/option and is otherwise
  inert (your `app.py` doesn't import it).
- **From the earlier clean-env audit (`handoff.md`)** — to make the suite pass without a local `.env`:
  1. `tests/e2e/conftest.py` → `os.environ.setdefault("TESTING", "true")` before importing `app`.
  2. `test_f3_special_chars_persona` → add the `mock_gemini` fixture arg.

## Intentionally NOT on this branch

Left on the local `capture-local-backup` branch (full original work) — say the word to push it:
- The full `app.py` rewrite (service extraction + server hardening: daemon threads, request timeout,
  Content-Length rejection, slowloris protection).
- Server/hardening tests (`test_adversarial.py`, the two `*_challenger.py`, `test_empirical_challenger.py`)
  and the `tests/e2e/` suite — these exercise that rewritten `app.py`, so they belong with the
  `app.py` reconciliation, not this clean drop.
- Overlapping files kept as **yours**: `app.py`, `character.py`, `Dockerfile`, `README.md`, `tests/test_app.py`.
