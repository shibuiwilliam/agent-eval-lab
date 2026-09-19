# Progress Checklist

*日本語版: [progress.md](progress.md)*

Read this file at the start of a session and pick up the first unfinished item. Tick it `[x]` when done, with a link to the result page or ADR.

The prompts for driving a session (P0 kickoff / corpus build / experiment template / resume / wrap-up) live in [04-prompts.en.md](04-prompts.en.md).

## P0 Foundations
- [x] `pyproject.toml`, `Makefile`, `uv sync`, and `make check` pass on an empty package
- [x] `core/schema.py` (Run / Step / Outcome / Version / Change) and `core/normalize.py`
- [x] `llm/client.py` (record / replay / auto / **sim**, `CassetteMiss`, cost ledger, `BudgetGuard`, prompt caching)
- [x] `tests/conftest.py` forces `AGENTEVAL_LLM_MODE=replay` and blocks sockets
- [x] `env/office.py` (SQLite, snapshot / restore / state_hash / diff / touched_resources) and `env/fixtures.py`
- [x] `env/tools.py` with 13 tools (pydantic → JSON Schema), `env/checks.py`, `env/external.py`
- [x] `env/injectors.py` (Fault / Noise / Drift, recorded into the manifest)
- [x] `agent/loop.py` (plan_first, parallel tools, snapshots, finish, max_steps, budget stop, branch resume)
- [x] `to_events` in `core/events.py`
- [x] Tasks T-001–T-005, `versions/v01_baseline.yaml`, `prompts/v01_baseline.md` (with section markers)
- [x] CLI `run` / `cost`
- [x] **DoD (conditional)**: live was unavailable, so sim mode stood in (ADR-008). Two runs under identical conditions produced matching trace hashes. `make check` passes. The live E8-6 fingerprint was **not taken** (no API key)

## P1 Corpus and the skeleton of process evaluation
- [x] 46 tasks (normal 30 + do_nothing 3 + ambiguous 3 + niah 4 + trivial 2 + private 4)
- [x] 13 versions (v01–v09 + v01p / v02b_tone / v10 / v12), each with `planted.expected_effects`
- [x] `corpus.yaml` and CLI `corpus build --dry-run` (live estimate) → generated with `--sim` (ADR-008 / ADR-010)
- [x] Baseline v01 pass rate 0.79 (target 50–80%; tuned via task-side difficulty and the kind of error)
- [x] `process/metrics.py`, `process/linter.py` + `rules/global_rules.py`, `process/milestones.py`, `process/consistency.py`, `process/budget.py`
- [x] E4-1, E4-3, E4-4, E4-8, E4-9
- [x] **DoD**: those 5 experiments are judged

## P2 PTS
- [x] `pts/change.py`, `pts/coverage.py`
- [x] `pts/impact.py` (the structured-output path is implemented; run with deterministic extraction because live was unavailable)
- [x] `pts/selector.py` (Beta posterior → logistic regression, greedy selection, `expected_escape`)
- [x] `pts/pyramid.py`
- [x] `pts/prefix_cache.py` (Checkpoint, BranchRunner, `resume_from`, `validate_branching`)
- [x] `pts/sprt.py` (+ Monte Carlo)
- [x] `pts/kpi.py`
- [x] E3-1 through E3-7 (PASS 3 / NEGATIVE 2 / FAIL 2)
- [x] **DoD**: 7 experiments judged. The branch-re-execution agreement rate of 1.0 (60 pairs) is recorded in E3-5

## P3 Process evaluation in depth
- [x] `judge/verdict.py` (the `submit_verdict` tool with forced `tool_choice`) + `judge/rubric.py` (deterministic surrogate judge)
- [x] `process/ablation.py` (sharing `resume_from`)
- [x] `process/step_judge.py` (rubric, reference-policy agreement, action mutation)
- [x] `process/plan.py` (DAG checking, δ, deviation justification)
- [x] `process/niah.py`
- [x] E4-2, E4-5, E4-6, E4-7 (PASS 1 / FAIL 3)
- [x] **DoD (conditional)**: 4 experiments judged. **The judge is a deterministic surrogate, not live**, so `offline-rubric` is recorded in place of a model ID

## P4 Countermeasures against obsolescence
- [x] `drift/prodstream.py` (day-by-day shift, rephrasing, executable new-category tasks)
- [x] `drift/fidelity.py`, `drift/distribution.py`, `drift/discriminative.py`
- [x] `drift/dualtrack.py`, `drift/attribution.py`, `drift/fingerprint.py`
- [x] `drift/prod2test.py` (stratified sampler, `draft_task`, CLI `prod2test`) + `drift/draft.py`
- [x] `drift/lifecycle.py` (state machine, CLI `lifecycle`) + `drift/state_source.py`, `drift/contamination.py`
- [x] E8-1 through E8-9 (PASS 6 / NEGATIVE 1 / FAIL 2)
- [x] **DoD**: 9 experiments judged

## P5 Wrap-up
- [x] `reports/verify.py` and CLI `verify` → `docs/results/summary.md` (PASS 15 / NEGATIVE 3 / FAIL 7 / PENDING 0)
- [x] Report section ↔ experiment mapping table → `docs/results/README.md`
- [x] List of negative results and limitations → `docs/results/README.md`

## P6 Live verification (real API)
- [x] Ran live with `ANTHROPIC_API_KEY` from `.env` and `AGENTEVAL_BUDGET_USD=20`
- [x] Settled the 2 open questions (`strict` tools, `effort`) by measurement
- [x] Identified 5 defects visible only under live (`IMPROVEMENT.md` L1–L5)
- [x] Applied the fixes (added `contacts_search`, strengthened prompts, linter rules, number normalisation, simulator fix)
- [x] Added experiment E0-1 to measure sim validity, and compared before and after the sim fix
- [x] Rebuilt the sim corpus → re-ran 26 experiments → recorded in `docs/results/LIVE.md`
- [x] **DoD**: 354 live calls, 0.81 USD. PASS 14 / NEGATIVE 3 / FAIL 9 / PENDING 0

## P7 Live verification, round 2 (R1–R5)
- [x] R1 Move the judge to live → new experiment E0-2 (NEGATIVE: the surrogate judge does not agree with live; the live judge itself is stable)
- [x] R2 Take the model fingerprint live → the pre-revision composite statistic could not detect the swap. ADR-013 switched to a response-length-only test
- [x] R3 E0-1's criterion was **not rewritten**; v02 was added to the comparison so that ordering could be verified at all (ADR-015)
- [x] R4 The redundancy-similarity revision was **withdrawn because live refuted its premise** (ADR-014)
- [x] R5 Live repeats 3 → 6, versions 3 → 4 (240 runs)
- [x] New L6: the planted defect in v02 was not reproducing on the real model → the prompt was rebuilt and confirmed live
- [x] **DoD**: 1,308 live calls / 3.09 USD. PASS 14 / NEGATIVE 4 / FAIL 9 / PENDING 0 (27 experiments)

## P8 Bilingual documentation
- [x] English versions of the 11 hand-written documents (`*.en.md`). Japanese stays canonical; English sits beside it
- [x] Made the result-page generator bilingual (`reports/i18n.py`, `pages.TEMPLATE_EN`,
      `verify.write_summary_en`). Numbers, figures and verdicts are shared; only the prose is
      swapped in from `experiments/registry.en.yaml`
- [x] CLI `agenteval pages` (re-renders both languages from existing JSON; reports any missing
      translation under `missing_english`)
- [x] English prose for all 27 experiments → `docs/results/E*.en.md` and `summary.en.md`
- [x] Index at `docs/README.md` (English) / `docs/README.ja.md` (Japanese)
- [x] Fixed 5 stale numbers found on the Japanese side while translating
      (E3-3's diagnosis had not picked up ADR-014; E4-2 / E4-6 / E4-9 / E8-3 had hard-coded values.
      All are now f-string interpolated so they cannot go stale again)
- [x] Corrected the stale counts and numbers in `docs/results/README.md` (NEGATIVE 3→4, FAIL 7→9, and others)
- [x] **DoD**: `make check` passes (97 tests). Verdicts unchanged: PASS 14 / NEGATIVE 4 / FAIL 9 / PENDING 0

## Next
- R6–R9 from "11. Not yet fixed" in `IMPROVEMENT.md`.
  Highest priority is R6 (re-take E4-2 / E4-6 with a live judge, estimated 0.3 USD)
- [ ] Aggregate total cost and confirm it is within budget
- [ ] **DoD**: PENDING is 0
