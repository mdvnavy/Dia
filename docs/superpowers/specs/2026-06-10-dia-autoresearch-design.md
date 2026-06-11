# DIA Autoresearch — Design

**Date:** 2026-06-10
**Status:** Approved by Navy (pending spec review)
**Inspiration:** [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — autonomous experiment loop: edit, run, measure one metric, keep/discard, repeat. Reference clone at `D:\_cultco\_agnt-ws\_repos\autoresearch`.

## Goal

Run an autonomous 2–3 hour experiment session where a coding agent iterates on DIA's
quality core (parsing, validation, scoring, document generation, agent instruction),
measured by a single deterministic score, and ends with a reviewable branch plus a
summary report. The human edits the program file; the agent edits the code.

## Components

Three new artifacts in the DIA repo. Nothing existing changes except a known-skip
marker on the flaky deploy-validation test.

### 1. `eval/golden.json` — golden expectations (read-only to the loop)

Per intake fixture in `tests/e2e/fixtures/inputs/` (14 files), encode:

- `expected_tier` — Quick Win / Custom AI Agent / Full Integration
- `expected_score_range` — `[min, max]` for `total_score`
- `expected_issues` — exact set of validation issue codes that SHOULD fire
- `must_parse` — fields the parser SHOULD extract (company name, pain point count, …)

Goldens are **aspirational, not a snapshot of current behavior**. For hard fixtures
(`malformed_tables_intake.md`, `special_characters_intake.md`, …) encode what the
pipeline should do, even where it currently fails. Target baseline: roughly 70–85 of
100, leaving headroom for the loop to climb.

### 2. `eval/run_eval.py` — the metric (read-only to the loop)

Runs every fixture through the deterministic pipeline (`parse → validate → score →
generate docs`), no API calls, no network. Prints a summary ending in one number.

**`dia_score` (0–100, higher is better):**

- **Golden correctness — 60 pts.** Per fixture: tier match, score in range, validation
  issue set match, parse extraction match. Equal weight per fixture, scaled to 60.
- **Doc quality rubric — 40 pts.** Per fixture, on the three generated documents:
  required sections present; no unfilled placeholders / `None` / empty values leaking
  into prose; key intake facts (company name, pain points, goals) referenced in the
  docs; length sanity bounds. Scaled to 40. Some rules intentionally fail at baseline
  (headroom).

Output format (greppable, autoresearch-style):

```
dia_score:       78.42
golden_pts:      51.20 / 60
rubric_pts:      27.22 / 40
fixtures:        14
runtime_seconds: 1.3
```

**Test gate (not points):** `python -m pytest tests/ -q` must pass before a score
counts. Any failure = automatic discard, like a crashed training run. The known-flaky
PS1 deploy-validation test gets a documented skip marker so it cannot poison the gate.

**Tie-break:** equal `dia_score` → prefer the simpler/smaller diff (autoresearch's
simplicity criterion).

### 3. `program-dia.md` — loop instructions (edited by the human, not the loop)

Adapted from autoresearch's `program.md`:

**Setup phase** (interactive, at launch):
1. Agree on run tag (e.g. `jun10`); branch `autoresearch/<tag>` must be fresh.
2. Create the branch in a dedicated worktree.
3. Read in-scope files: `README.md`, `client_discovery/core.py`, `character.py`,
   `eval/run_eval.py` (read-only), `eval/golden.json` (read-only).
4. Run the eval once; record the baseline row. Create untracked `results.tsv`.
5. Confirm setup with the human, receive the **stop time**, then go autonomous.

**Experiment loop:**
1. Pick one idea (one idea per experiment, small reviewable diff).
2. Edit only the allowed surface (see Guardrails).
3. Commit.
4. Run the test gate, then `python eval/run_eval.py > eval.log 2>&1`; grep `dia_score`.
5. Improved → keep, advance the branch. Equal or worse → `git reset --hard` back.
6. Log to `results.tsv` (untracked, tab-separated):
   `commit	dia_score	tests	status	description`
   (`status` ∈ keep / discard / crash; crashes log score 0).
7. Crash policy: dumb bug → fix and rerun; fundamentally broken idea → log crash, move
   on. Runaway run (> 5 min) → kill, treat as crash.

Seed idea menu (agent generates its own beyond these): parsing aliases and table
recovery for malformed fixtures; scoring threshold tuning against goldens; richer doc
templates (fill rubric gaps); validation coverage; instruction-prompt tightening in
`character.py`.

**Wrap-up phase** (replaces autoresearch's NEVER-STOP):
1. At the stop time, finish the in-flight experiment only.
2. Optional: 2–3 live Gemini smoke calls (`/api/agent` path) to confirm prompt edits
   didn't break the live path — bounded to respect the ~20/day free-tier quota.
3. Write `docs/autoresearch/<tag>-report.md`: baseline vs final score, keeps with
   one-line rationales, interesting discards, suggested next-run ideas.
4. Halt on the branch. No merge, no push. Human reviews and merges what they like.

## Guardrails

| Surface | Loop may touch? |
|---|---|
| `client_discovery/core.py` | ✅ yes |
| `character.py` instruction block (lines ~96–113) | ✅ yes |
| `eval/` (harness + goldens) | ❌ read-only |
| `tests/` | ❌ read-only |
| `app.py`, `agent_runtime.py`, frontend, deploy scripts | ❌ read-only |
| Dependencies (`requirements.txt`, `pyproject`) | ❌ frozen |
| Merging to main / pushing | ❌ never |

The eval is ground truth; if the agent believes a golden is wrong, it logs the
disagreement in the report rather than editing the golden.

## Expected throughput

Eval ≈ 1–2 s, test gate ≈ 31 s, agent think/edit/commit ≈ 2–4 min → **~40–60
experiments in a 2–3 hour window**, comfortably past diminishing returns for a
codebase this size.

## Error handling

- Test-gate failure or eval crash → experiment discarded, branch reset, row logged.
- Eval harness itself crashing on a mutated pipeline (e.g. parse exception) → caught
  per-fixture; fixture scores 0 for that run rather than killing the harness.
- Agent stuck (3+ consecutive crashes on one idea) → abandon idea, log, move on.

## Testing the harness itself (one-time, at build)

- `run_eval.py` on the untouched repo → stable baseline score, reproducible across two
  consecutive runs (determinism check).
- Sabotage check: hand-break one fixture's scoring path → score drops; revert → score
  restores. Confirms the metric actually moves.
- Test gate honors the known-skip and otherwise passes clean on Navy's machine
  (venv at main repo root — see dev-environment notes).

## Out of scope

- ADK-native `.evalset.json` evals (burns Gemini quota per iteration).
- LLM-judge scoring (subjective, drifts overnight; revisit later if rubric saturates).
- Frontend/UX experiments.
- Any scheduled/cloud runner — the harness is a locally launched session.
