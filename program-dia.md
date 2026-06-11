# DIA Autoresearch Program

You are an autonomous research agent improving DIA (Discovery Intake Agent).
You experiment on the quality core, measure with one number, keep what works,
discard what doesn't. The human edits THIS file; you edit the code.

## The metric

`python eval/run_eval.py` prints `dia_score` (0-100, HIGHER is better) in
about a second, using only the deterministic pipeline -- no API calls.
Extract it with: `grep "^dia_score:" eval.log`

Baseline at harness creation: 88.99. The reward gradient lives mostly in the
rubric (rubric_pts, 40-pt axis): document grounding and placeholder hygiene.
The golden axis (60 pts) is nearly saturated by design -- it is regression
armor, plus one aspirational bounty: recovering the company name from the
malformed-tables fixture (+1.67).

**Test gate:** before the eval counts, run:
`python -m pytest tests/ -q -m "not baseline_pin"`
It must report 0 failed. Any failure = the experiment is discarded, period.
(The `baseline_pin` deselection exists ON PURPOSE: those tests pin baseline
behavior the experiments are supposed to change. Never run the gate without
the marker filter, and never edit the tests.)

**Honest constraint on scoring experiments:** the gate's core tests pin the
exact total_score of every fixture (e.g. complete intake = 12). Scoring-rule
changes that shift fixture totals WILL fail the gate and get discarded.
Prefer parsing, validation-message, and document-template experiments;
attempt scoring changes only if they preserve all fixture totals.

## Setup (interactive, do this with the human)

0. Environment check: bare `python` on this machine may resolve to the
   system interpreter, not the project venv. The venv lives at the MAIN
   repo root: `D:\_cultco\_agnt-ws\_repos\client-discovery-agent-adk\.venv`.
   Activate it (`.venv\Scripts\activate`) or use its python.exe explicitly
   for EVERY `python` command in this program. Verify before baseline:
   `python -c "import sys; print(sys.prefix)"` must print the venv path.
1. Agree on a run tag from today's date (e.g. `jun10`). The branch
   `autoresearch/<tag>` must NOT already exist -- this is a fresh run.
2. Create the branch: `git checkout -b autoresearch/<tag>`.
3. Read the in-scope files for full context:
   - `README.md` -- project context
   - `client_discovery/core.py` -- parsing, validation, scoring, doc templates (YOU EDIT THIS)
   - `character.py` -- the agent instruction block, lines ~96-113 (YOU MAY EDIT THE INSTRUCTION TEXT ONLY)
   - `eval/run_eval.py` and `eval/golden.json` -- the metric (READ-ONLY)
4. Run the gate then the eval once; this is your baseline. Create
   `results.tsv` (untracked -- never commit it) with header + baseline row.
5. Confirm setup with the human and receive the STOP TIME. Then go autonomous.

## What you CAN do

- Edit `client_discovery/core.py`: parsing aliases, table extraction,
  validation rules, document templates -- all fair game (scoring: see the
  honest constraint above).
- Edit the instruction text inside `character.py` (the prompt block only).

## What you CANNOT do

- Modify anything in `eval/` or `tests/` or `pytest.ini` -- the metric and
  gate are ground truth. If you believe a golden entry or test is wrong,
  write the disagreement in your final report; do not touch the file.
- Modify `app.py`, `agent_runtime.py`, the frontend, deploy scripts.
- Add or change dependencies.
- Merge to main or push anywhere. The run lives and dies on its branch.
- Call any external API. The loop is fully offline.

## The experiment loop

LOOP UNTIL STOP TIME:

1. Pick ONE idea. Small, reviewable diff. One idea per experiment.
2. Edit the allowed files.
3. Commit with a descriptive message.
4. Gate: `python -m pytest tests/ -q -m "not baseline_pin" > gate.log 2>&1`.
   Any failure -> the experiment is dead: `git reset --hard HEAD~1`, log
   status `discard` with the failure noted, next idea.
5. Eval: `python eval/run_eval.py > eval.log 2>&1`, then
   `grep "^dia_score:" eval.log`.
6. Higher than the best-so-far -> KEEP (the commit stays, branch advances).
   Equal or lower -> `git reset --hard HEAD~1`.
7. Append a row to `results.tsv` (tab-separated, 5 columns):
   `commit  dia_score  tests  status  description`
   - commit: short hash (7 chars); for discarded commits, the hash before reset
   - dia_score: e.g. 88.99; use 0.00 for crashes
   - tests: `pass` or `fail`
   - status: `keep`, `discard`, or `crash`
   - description: one short sentence, no tabs
8. Go to 1.

Crash policy: dumb bug (typo, missing import) -> fix and rerun once or
twice. Fundamentally broken idea -> log `crash`, reset, move on. Stuck 3+
consecutive crashes on one idea -> abandon it. Any single run exceeding
5 minutes -> kill it, treat as crash.

Idea seeds (generate your own beyond these): recover rows from malformed
tables (see `malformed_tables_intake.md` -- rows missing the closing pipe;
the golden bounty); extend QUESTION_ALIASES coverage; make doc templates
cite actual pain points and goals verbatim (the rubric rewards grounding);
replace placeholder leakage (TBD / None listed / Unknown) with SUBSTANTIVE
graceful phrasing -- not a synonym swap; richer validation messages.

A note on substance: renaming "TBD" to "n/a" technically clears the rubric
check but is a hollow win. The human reviews every kept diff at wrap-up and
will judge placeholder fixes on whether the replacement text genuinely
helps a reader of the generated documents. Hollow keeps get reverted by the
human; spend your experiments on substance.

Use `python eval/run_eval.py --verbose` when you want per-fixture failure
detail to aim your next idea.

## Wrap-up (at STOP TIME -- this replaces "never stop")

1. Finish only the experiment already in flight. Start nothing new.
2. Write `docs/autoresearch/<tag>-report.md`:
   - baseline vs final dia_score
   - every KEEP with a one-line rationale
   - the most interesting discards (what you learned)
   - golden entries or pinned tests you disagree with, if any
   - suggested ideas for the next run
3. Commit the report to the run branch. Do NOT merge. Do NOT push.
4. Print a 5-line summary to the console and halt.
