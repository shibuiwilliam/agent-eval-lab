# Code and Experiment Review, and the Re-run

*日本語版: [REVIEW.md](REVIEW.md)*

Date: 2026-09-19 / Scope: all 70 files under `src/agenteval/`, the 27 experiments in `experiments/`,
the plan, catalogue and ADRs under `docs/plan/`, and the source report
`docs/report/ai_agent_evaluation_report.md`.

This review asks whether the code that verifies evaluation methods is itself good enough to verify
anything. **A defect in the evaluator is harder to find than a defect in the agent under test, and
it flips conclusions wholesale.** Live verification (`IMPROVEMENT.md`) found 3 of them; reading the
code found 8 more.

## 0. Outcome

| | Before the re-run | After |
|---|---|---|
| PASS | 14 | **15** |
| NEGATIVE | 4 | **5** |
| FAIL | 9 | **7** |
| PENDING | 0 | 0 |

Three verdicts changed. In every case what was fixed is **the thing being compared against the
criterion, not the criterion**.

| Experiment | Change | Why |
|---|---|---|
| E3-6 SPRT | NEGATIVE → **PASS** | The comparator's trial count used the two-sample formula. The one-sample form gives 10 instead of 9, moving the ratio from 0.604 to 0.544 (ADR-023) |
| E3-3 Selection | FAIL → **NEGATIVE** | The cause is corrected from "our redundancy implementation is at fault" to "the method does not work as claimed". Isolated by comparing 5 policies |
| E3-7 KPI | FAIL → **NEGATIVE** | Same as E3-3 |

**Not one criterion in `experiments/registry.yaml` was changed.**

---

## 1. Defects in the evaluator

### A1 [critical] The flip threshold compared quantities of different dimensions → ADR-016

`_common.flake_band()` returned the Wald 95% half-width of v01's **marginal pass rate**, and
`flipped_tasks()` used it as a threshold on the **paired disagreement rate**. One is "how much does
the pass rate wobble"; the other is "how often does the outcome change on the same (task, repeat)
pair". Different dimensions.

Worse, sim is deterministic in `(task_id, seed, repeat)` and reuses the same luck across versions,
so under the null hypothesis "this version has no effect" the disagreement is **exactly 0**. There
is no basis for a threshold at all.

| Change | Before (with band) | After (no band) | Flips missed |
|---|---|---|---|
| v09_model_swap | 4 | **23** | 19 (83%) |
| v06_toolschema_v2 | 6 | **11** | 5 (45%) |
| v02_dateformat | 10 | 10 | 0 |
| v05_unsafe_delete | 2 | 2 | 0 |

Tasks near a pass rate of 0.5 got a threshold of 0.31, so the method **destroyed the most signal
exactly on the boundary tasks that carry the most information**. Five experiments were affected:
E3-1 / E3-3 / E3-4 / E3-7 / E8-5.

After the fix, E3-1's `recall_of_flipped = 1.0` must contain **all 11** flips rather than 6. It
still passes, so **that experiment's result is now a stronger claim than before**.

### A2 [critical] The escape-defect rate diverged from the report and was incomparable with its own prediction → ADR-018

Report 3.8 defines `Escape(S) = |F_full \ S| / |F_full|` with `F_full` = "the **failures** found by
a periodic full run". The implementation set `F_full` to "the tests that **flipped**".

Meanwhile `selector.expected_escape` computes the failure mass `sum(1 - p_hat_t)` — it predicts
failures, exactly as the report defines. So E3-3's `calibration_error` was subtracting a prediction
about failures from a measurement about flips. **Two incompatible quantities, so the metric could
not mean anything either way.**

Aligned on failures, the calibration error is **0.325 → 0.080** (criterion 0.15). The selector was
correctly estimating how many failures it leaves behind all along.

### A3 [critical] The detour rate diverged from the report's definition → ADR-017

Report 4.2 defines it as `D = L_actual / L_min` (a ratio ≥ 1). The implementation computed
`(steps − l_min) / steps` (a fraction in [0,1)) — a different quantity. And when `l_min` was
missing it was treated as 0, pinning `D` at 1.0. Now split into `detour_ratio` (per the report) and
`detour_excess` (the pre-revision fraction). It is not part of any pass criterion.

### A4 [critical] Stop appropriateness diverged from the report's definition → ADR-019

Report 4.2 defines it as "the rate of declaring completion while unmet, and the rate of continuing
after achieving". The implementation only checked **whether the last tool call was `finish`**,
never referencing achievement at all.

This was distorting E0-1's conclusion. Before the fix, sim 1.0 / live 0.66 was the headline example
of "a quantity where sim does not match live". Measured per the report:

| Metric | live | sim | Agree? |
|---|---|---|---|
| `stop_appropriate` (the report's definition) | 0.496 | 0.500 | **yes** |
| `premature_stop` (declared done while unmet) | 0.471 | 0.450 | **yes** |
| `finish_called` (the pre-revision quantity) | 0.683 | 1.000 | no |

**"Sim cannot reproduce stop appropriateness" was wrong.** What it could not reproduce was the
**tool-calling convention**, not the appropriateness of stopping. The two are now separate metrics.

### A5 [moderate] Saturation diverged from the rules

`.claude/rules/drift.md` says saturation requires a pass rate > 0.95 **under every version** plus
|d| < 0.1. The implementation used the rate **pooled** across all runs, so a test could be judged
saturated while one version scored low. Aligned to the rule.

---

## 2. Defects in the experiment design

### B1 [critical] The control was not a control → ADR-020

`selector.select()` always includes the 3 inviolable tasks (T-005 / T-011 / T-025) and spends their
budget. The controls `select_random()` / `select_recent_failures()` did not. **The comparison looked
like equal budgets, but only one side was forced to carry 3 specific tasks.** The controls now apply
the same rule.

### B2 [critical] Branch re-execution billed the divergent step twice → ADR-021

`BranchRunner.run()` counted the decision confirmation at the divergent step as billable, then
immediately re-sent **that same step** to the model via `resume_from=divergence`. The confirmation's
response can be reused when resuming, so it over-counted by one step per branch.

E3-5's `live_step_ratio_v02` moved **1.113 → 1.000**. The verdict stays NEGATIVE (criterion 0.6) and
the structural conclusion — retracing a prefix costs one billed confirmation per step — is unchanged.

### B3 [moderate] Uncertainty was estimated from a history that does not react to the change → ADR-022

E3-3 / E3-7 used `history = [r for r in runs if r.version_id != target]`, pooling **11 versions** to
estimate `p_hat_t`. Report 3.4's "recent pass rate" means the current deployment's history. Pooling
11 versions makes `p_hat` nearly identical across events, so **the selection stops reacting to the
change Δ at all**. The history is now restricted to v01_baseline.

### B4 [critical] SPRT's comparator used the two-sample formula → ADR-023

`fixed_n_for_power()` used the pooled variance `((p0+p1)/2)` in the type-I term. That is the formula
for comparing **two** proportions. The test here is a **one-sample, simple-vs-simple** test of
`H0: p = p0` against `H1: p = p1`, so the null variance must be `p0(1−p0)` (the implementation mixed
a two-sample alpha term with a one-sample beta term — neither formula).

For p0=0.5, p1=0.9, α=0.05, β=0.10: **fixed-n = 9 (wrong) / 10 (right)**, moving the ratio from
**0.604 to 0.5436** and meeting the 0.6 criterion.

Not one character of the criteria changed. What changed is the **measuring instrument**, and the
argument for it (the null variance of a one-sample test is the null's) holds without looking at the
verdict.

### B5 Nobody had checked whether the criteria were attainable

Nobody had checked whether E3-7's "escape-defect rate ≤ 0.1 at a 40% budget" is reachable. An oracle
that knows each repeat's `F_full` in advance now bounds it:

| Budget | Oracle (attainable floor) |
|---|---|
| 5% | 0.966 |
| 20% | 0.418 |
| 30% | 0.155 |
| 40% | **0.031** |
| 100% | 0.000 |

At 40% the floor is 0.031, so **the criterion of 0.1 is attainable** — what falls short is the
selection. Conversely E8-7's lift ≥ 3.0 is **unattainable** (uniform sampling already captures 45%
important sessions, capping lift at 2.2); that stays FAIL as an experiment-design error.

---

## 3. Process defects

- **C1 Deviations from the report had no ADR** (A2 / A3 / A4 / A5), violating CLAUDE.md's "write an
  ADR when you deviate from the report". ADR-016 through ADR-023 were added.
- **C2 The plan contained mutually tense requirements.** `00-plan.md` makes "baseline pass rate
  50–80%" a P1 definition of done, while E3-7 demands "escape-defect rate ≤ 0.1 at a 40% budget".
  In a suite with a high failure rate, `|F_full|` grows relative to the budget and Escape cannot
  fall. They happened to be compatible here (the 40% floor is 0.031), but **having both in the same
  plan is a design tension** and is now noted in `00-plan.md`.
- **C3 Stale hard-coded numbers had survived on result pages** (5 fixed in the previous session, now
  f-string interpolated so they cannot go stale again).

---

## 4. What the re-run showed

### 4.1 Uncertainty-driven selection cannot beat random here (E3-3, NEGATIVE)

After the fixes, 5 policies were compared on both KPIs. Figures are ratios against random selection;
**below 1 means better than random**.

| Policy | Failure-KPI ratio | Flip-KPI ratio |
|---|---|---|
| The report's pipeline (coverage-first + redundancy) | 1.056 | 1.137 |
| Redundancy penalty removed | 0.948 | 0.622 |
| `H(p̂)/c` alone | 1.010 | 0.973 |
| Failure probability `(1−p̂)/c` | 0.760 | 0.797 |
| **Coverage overlap alone** | 0.839 | **0.344** |

What this says:

1. **In this environment the signal lives in report 3.2 (coverage), not 3.4 (uncertainty).**
   Coverage overlap alone is the best on the flip KPI at 0.344× random; `H(p̂)/c` alone is 0.973× —
   essentially random.
2. **The redundancy penalty destroys signal.** Removing it moves 1.137 → 0.622. Per ADR-014 the
   cause is the monotony of sim trajectories, not the similarity definition.
3. Even so, the report's full pipeline misses the 0.5 criterion. The pre-revision diagnosis was FAIL
   (our implementation); since **`H(p̂)` alone cannot beat random even without redundancy**, the
   cause is corrected to the method side and the verdict becomes NEGATIVE.

It is theoretically coherent. Escape is minimised by selecting the tests most likely to fail, but
`H(p̂)` peaks at `p̂ = 0.5` and therefore **deprioritises the tests that almost certainly fail
(`p̂ → 0`)**. Maximising information gain (3.4's objective) and minimising the escape-defect rate
(3.8's KPI) are different optimisation problems. **The report presents them together as "the
selector's KPI", but optimising one does not optimise the other.**

### 4.2 Calibration did hold (E3-3)

With both sides on the same quantity, the error is 0.080 (criterion 0.15). **The selector correctly
estimates how many failures it is leaving behind.** The pre-revision 0.325 was an artefact of
subtracting incompatible quantities.

### 4.3 SPRT's claims did hold (E3-6, PASS)

α = 0.0455 (≤ 0.07), β = 0.0516 (≤ 0.12), mean trials 0.544× fixed-n (≤ 0.6). But that is with
`p_true = 0.5` — the slowest point to settle — in the grid. Any claim that "SPRT uses at most 60% of
fixed-n" must state the hypothesis setup (p0, p1) and the range of `p_true`.

### 4.4 The savings from branch re-execution do not hold (E3-5, still NEGATIVE)

Even with the double-billing fixed, the live step ratio is 1.000, far from the 0.6 criterion. Report
3.6 says "execution cost falls from trajectory length L to suffix length L − i*", but **retracing a
prefix requires one decision confirmation per step, and once the version changes that is always
billed.** The report's cost model does not account for that confirmation cost. The token ratio is
1.024 — lower than the step ratio — so the prefix cache shows up only on the token side.

---

## 5. Left unfixed

| # | Item | Why not now |
|---|---|---|
| R10 | E4-2's ceiling effect (U is 0.889 even for v01) | Needs a version without a repair loop; adding versions changes the planting design |
| R11 | E4-4's Spearman over 4 versions (p = 0.728) | The sample size the criterion demands is not met. Adding versions changes the criterion, so it needs an ADR |
| R12 | E4-7's niah tasks run only 2–3 steps | Needs multi-stage tasks (a task-design change) |
| R13 | E8-7's lift criterion is unattainable | Rewriting it as an absolute capture rate needs an ADR |
| R14 | E8-9's gap is a simulator free parameter | Can only be measured live (about 3 USD) |
| R15 | Sim trajectory diversity (13 distinct tool-name sequences) | Requires rebuilding the task set. E3-3's redundancy behaviour depends on this |

None are done, because each would mean **tweaking the implementation to meet a criterion**
(ADR-007).
