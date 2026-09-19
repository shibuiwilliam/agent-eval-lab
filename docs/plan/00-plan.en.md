# Overall Plan — Illustrative Implementation and Verification of Evaluation Methods

*日本語版: [00-plan.md](00-plan.md)*

## Goals and non-goals

**Goal**: implement the methods from Chapter 3 (PTS), Chapter 4 (process and plan evaluation) and Chapter 8 (countermeasures against test obsolescence) of the report on top of a small, controllable agent and environment, and confirm numerically that each one **reacts to a planted defect and stays quiet on the control**. As a by-product, record as negative results the claims of the report that did not hold.

**Non-goals**: a production-quality evaluation platform, a general-purpose framework, a UI, multi-agent work, or full coverage of Chapters 5–7 (the judge is implemented only to the minimum needed to verify Chapter 4).

## What is being verified

| Ch. | Method | Experiment | Main provenance |
|---|---|---|---|
| 3 | Trajectory coverage dependency graph | E3-1 | replay |
| 3 | Semantic impact estimation | E3-2 | live (a little Haiku) |
| 3 | Uncertainty-driven selection | E3-3 | replay + simulated |
| 3 | Test-pyramid level decision | E3-4 | replay |
| 3 | Prefix cache and branch re-execution | E3-5 | live |
| 3 | SPRT early stopping | E3-6 | simulated + a little live |
| 3 | Escape-defect rate, ε-exploration, inviolable set | E3-7 | replay |
| 4 | Trajectory metrics | E4-1 | replay |
| 4 | Ablation-based wasted-call detection | E4-2 | live |
| 4 | Trajectory linter | E4-3 | replay |
| 4 | Partial-order milestones | E4-4 | replay |
| 4 | Step-level judging | E4-5 | live (Sonnet) |
| 4 | Plan externalisation and deviation rate | E4-6 | replay + a little live |
| 4 | In-trajectory NIAH | E4-7 | live |
| 4 | pass@k / pass^k and consistency | E4-8 | replay |
| 4 | Cost budgets and Goodhart pairs | E4-9 | replay |
| 8 | Mock fidelity and TTL | E8-1 | replay + external-service comparison |
| 8 | Freshness and distribution distance | E8-2 | simulated + synthetic prod |
| 8 | Discriminative power and saturation | E8-3 | replay |
| 8 | Dual-track κ | E8-4 | live |
| 8 | Drift attribution | E8-5 | live (branch re-execution) |
| 8 | Model fingerprinting | E8-6 | live |
| 8 | Production→test pipeline | E8-7 | live (synthetic prod) |
| 8 | Lifecycle state machine | E8-8 | unit (no API) |
| 8 | Contamination detection | E8-9 | replay |

Details are in [02-experiment-catalog.en.md](02-experiment-catalog.en.md).

## Corpus plan (`corpus.yaml`)

- 30 tasks (scheduling 10 / mail 8 / files 8 / mixed 4) + edge cases (do_nothing 3, ambiguous 3) + niah 4 + trivial 2 + private 4
- 9 versions (v01–v09) plus the contaminated version v10. Repeats: 5 for the baseline, 3 for the rest
- Rough call count: 30 × 9 × 3 ≈ 810 runs × ~7 steps ≈ 5,700 calls (Haiku). Judge and reference policy (Sonnet) stay within 1,500 calls total across E4-2, E4-5 and E8-7
- Rough cost: 20–30 USD on the Haiku side, 20–40 USD on the Sonnet side. **Overall budget 120 USD**, set in `AGENTEVAL_BUDGET_USD`. Per-experiment ceilings live in `registry.yaml`
- Unit prices come from `pricing.yaml`. Prompt caching should bring input cost below the estimate. Always refresh the estimate with `corpus build --dry-run`

## Tuning baseline difficulty

Adjust the tasks so that the baseline v01 pass rate lands between 50% and 80%. A suite where everything passes — or everything fails — cannot verify PTS or discriminative power. Do the tuning on the task side (ambiguity, required step count, fault injection); never fix it by switching to a stronger model.

## Phases and definitions of done (DoD)

### P0 Foundations
- `LLMClient` (record/replay, cost ledger, BudgetGuard, prompt caching)
- The `office` environment (SQLite, snapshot/restore/hash/diff), 13 tools, the skeleton of the injectors
- The agent loop, the `Run` schema, `to_events`
- 5 tasks, v01_baseline, the `run` CLI
- **DoD**: record 5 tasks × v01 live once each, replay them twice, and get identical trace hashes. `make check` passes. `pytest` passes with no API.

### P1 Corpus and the skeleton of process evaluation
- 30 tasks + edge cases, versions v01–v09, `corpus build` (dry-run → live)
- `metrics.py`, `linter.py`, `milestones.py`, `consistency.py`, `budget.py`
- **DoD**: E4-1, E4-3, E4-4, E4-8, E4-9 are PASS or NEGATIVE and have result pages. The baseline pass rate is within 50–80%.

### P2 PTS
- `change.py`, `coverage.py`, `impact.py`, `selector.py`, `pyramid.py`, `prefix_cache.py`, `sprt.py`, `kpi.py`
- **DoD**: E3-1 through E3-7 are all judged. The validity check for branch re-execution (agreement rate) appears on the result page.

### P3 Process evaluation in depth
- `ablation.py`, `step_judge.py`, `plan.py`, `niah.py`
- **DoD**: E4-2, E4-5, E4-6, E4-7 are judged. The judge's model ID and call count appear on the result pages.

### P4 Countermeasures against obsolescence
- `prodstream.py`, `fidelity.py`, `distribution.py`, `discriminative.py`, `dualtrack.py`, `attribution.py`, `fingerprint.py`, `prod2test.py`, `lifecycle.py`, `contamination.py`
- **DoD**: E8-1 through E8-9 are judged.

### P5 Wrap-up
- Generate `summary.md` with `agenteval verify`. List the negative results and the limitations. Produce a table mapping each section of the report to "verified / not verified / conditional".
- **DoD**: every experiment is PASS, FAIL or NEGATIVE with no PENDING. Total cost is within budget.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Haiku solves too few — or too many — of the tasks | Tune difficulty on the task side. A 50–80% pass rate is part of the P1 DoD |
| The model is updated mid-project | Pin the model IDs and take an E8-6 fingerprint once during P0. Write an ADR if it changes |
| Rate limits | Parallelism of at most 4. Batch only independent calls |
| Replay expectations drift (live varies even for an identical request) | Replay exists for determinism; handle live variance with repeats. Never compute statistics that mix the two |
| The temptation to loosen criteria when verification does not work out | Criteria changes require an ADR. Treat NEGATIVE as a legitimate result |
| Cassettes and snapshots growing without bound | `data/` is git-ignored. Provide `agenteval data prune --older-than` |

## Success criteria for the project as a whole

- All 25 experiments are judged (no PENDING)
- Each chapter honestly records at least one "negative or conditional" result (an all-PASS suite is suspicious)
- Total cost stays within 120 USD
- A table mapping report sections to experiments exists in `docs/results/summary.md`
