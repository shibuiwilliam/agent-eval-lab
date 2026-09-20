# Rebuilding and Verifying Chapter 3's PTS

*日本語版: [PTS.md](PTS.md)*

Dates: 2026-09-20 / 2026-09-21 · Scope: report sections 3.2–3.8 (Predictive Test Selection)

E3-3 produced a negative result: selecting by uncertainty `H(p̂)` cannot beat random. But **PTS is a
mechanism with demonstrated industrial value**, so it mattered whether that negative was a limit of
the method or a flaw in how we built it. It was the latter — and the decisive missing piece was
**recording change history and test history at all**.

## 0. Outcome

| | Before the rebuild | After |
|---|---|---|
| PASS | 15 | 15 |
| NEGATIVE | 5 | **6** |
| FAIL | 7 | **9** |
| Experiments | 27 | **30** |

**Report 3.4's claim decomposes into three. Separated, it is clear which halves hold.**

| Claim | Experiment | Result |
|---|---|---|
| (a) `p̂_t` estimated from features is usable for selection | **E3-8** (new) | **Mostly holds**: 4 of 5 criteria met (the miss is a near-unattainable bar) |
| (b) Selecting the highest-uncertainty tests is good | E3-3 | **Does not hold** (worse than random) |
| (c) ε-exploration keeps the selector calibrated (3.8) | **E3-9** (new) | **Neither confirmed nor refuted** (effect below measurement error) |
| (d) Recency from change/test history is a useful feature | **E3-10** (new) | **Lag works** (AUC 0.878, above coverage's 0.776); too little history to select feature groups |

E3-3's hypothesis and criteria were **not rewritten** (ADR-025). Rewriting a failed hypothesis is
"tweaking until it matches"; the correct move is to state the decomposed claims as new experiments.

### The decisive fix was recording change and test history

Industrial PTS learns from changed files, changed locations, **the lag since the previous change**,
and **change size**. This project had none of them. The cause is structural: **lag cannot be defined
without an ordering over changes**, and the implementation treated changes as an unordered set
(ADR-028).

After adding an append-only ledger (`pts/history.py`) with 6 lag features, 5 test-history features,
4 cross features and 11 churn features, E3-8 changed as follows.

| Metric | No history features (37 changes) | **With history, lag and churn (65 changes)** | Criterion |
|---|---|---|---|
| ROC AUC | 0.826 | 0.814 | ≥ 0.75 ○ |
| **Regression recall at a 30% budget** | 0.535 | **0.819** | ≥ 0.80 **○** |
| **Escape-defect rate vs random** | 0.868 | **0.319** | ≤ 0.5 **○** |
| Recall ratio vs random | 1.155 | 1.756 | ≥ 2.0 × |
| Control (random recall) | 0.463 | 0.467 | ≤ 0.6 ○ |

**Four of five criteria now hold.** The only miss, `recall_ratio_vs_random ≥ 2.0`, demands an
absolute recall of **0.933** when the in-budget ceiling is **0.934** — it asks for oracle-level
performance (discussed below).

---

## 1. The root cause: there was no training data

Report 3.8 **spells out the cold-start procedure**:

> When there is no training data (change → outcome flip), create flip data by deliberately
> introducing synthetic changes such as prompt perturbations and model swaps. (report 3.8)

**The project had never done this.**

| | Originally | Synthetic v1 | **With history (current)** |
|---|---|---|---|
| Change events | 8 | 37 | **65** |
| ...that produce regressions | 3 | 18 | **21** |
| Training rows | 368 | 1,702 | **2,990** |
| Positives (regressions) | — | 108 (4.2%) | **122 (4.1%)** |
| Features | 5 | 57 | **85** (17 history + 11 churn added) |
| Units changed 2+ times | — | few | **23 of 28** |

Breakdown of the 65 changes: 29 prompt (drop / truncate / emphasize per section), 7 config, 1 tool
schema, 1 model swap, **24 tool faults** (12 tools × always / from-step-2), 3 two-factor.

**Changing the same unit repeatedly is what separates this from v1.** If a unit is only ever changed
once, every lag reads "first time" and the feature is dead. `max_steps` is changed 7 times,
`date_format` 5 times, `verification` / `safety` / `clarify` 4 times each. As a result
`lag_since_units_changed` has a median of 3 and a range of 1–62.

## 2. Five defects found during the rebuild

### D1 [critical] Fault injection executed the tool and only then replaced the response → ADR-026

`_execute_tool` ran the tool **against the environment first**, then passed the response to
`FaultInjector`. Since `fault="error"` only swaps the response, **the write succeeded while
"failing"** — the agent saw an error and behaved as if nothing was created, yet `event_exists(...)`
looked at the DB and returned True.

| Faulted tool | Kind | Regressed tasks (before → after) |
|---|---|---|
| calendar_search / mail_search / file_read / external_lookup | read | 11 / 4 / 5 / 5 → unchanged |
| calendar_create | write | **0 → 10** |
| mail_send | write | **0 → 11** |
| file_write | write | **0 → 9** |

It surfaced because **all six write-tool faults were cleanly zero**.

### D2 [critical] Config changes were indistinguishable to the model

`max_steps=3` and `max_steps=6` had **identical feature vectors**, making config changes unlearnable
in principle. Cross features were added (`max_steps_headroom = max_steps − the test's mean steps`).

### D3 [critical] The model was hard-coded

The first implementation pinned GBDT, which overfits ~100 positives. Model choice now happens by
**nested cross-validation inside the training folds only**.

### D4 [critical] Evaluation and training operated on different units → ADR-027

Evaluation used global AUC, but **selection happens per change**. A feature constant within a change
cannot contribute to ranking within it. Global and within-change AUC were moving in opposite
directions. Within-change percentile ranks were added.

### D5 [critical] With no ordering over changes, not one lag feature could be written → ADR-028

The two groups industrial PTS weighs most heavily were **missing wholesale**:

- **Recency (lag)**: changes since this test last ran / last failed; since this unit was last changed
- **Change size (churn)**: lines added / removed, character delta, units touched

The cause is structural, as above. Even report 3.4's "flip count" is a history feature — "over the
last k changes" only means something once an ordering exists, and the implementation had collapsed
it into an average over all history.

`pts/history.py` now holds an append-only ledger (`ChangeRecord` / `TestRunRecord`), and
`History.features(seq, task_id, units)` sees **only records before `seq`**, so temporal leakage
cannot occur.

---

## 3. E3-8: with history features, selection works too (4 of 5 criteria)

### Learning works

| Metric | Value | Criterion |
|---|---|---|
| ROC AUC (family-level leave-one-group-out) | **0.814** | ≥ 0.75 ○ |
| ROC AUC (single-change hold-out, diagnostic) | 0.813 | — |
| Within-change AUC | 0.833 | — |
| Coverage-only AUC | 0.755 | — |
| Historical failure rate only (change-blind control) | **0.569** | — |

The change-blind control sits near chance, confirming that **the change information is what makes
prediction possible**.

### Selection works now too

Recall of regressions at a 30% budget (in-budget ceiling 0.934):

| Policy | Recall | Escape-defect rate |
|---|---|---|
| **Learned model** | **0.819** | **0.169** |
| Coverage overlap only | 0.795 | — |
| Equal-weight rank fusion (diagnostic) | 0.733 | 0.269 |
| Uncertainty `H(p̂)` | 0.518 | — |
| Historical failure rate only | 0.474 | — |
| Random | 0.467 | 0.529 |

**The escape-defect rate is 0.319× random** (criterion 0.5) — well below the level E3-3 could not
reach. Before the history features it was 0.868×, essentially indistinguishable from random.

The family breakdown is where the change from v1 shows most clearly:

| Family | Changes | Learned (with history) | Learned (v1, no history) | Coverage | Ceiling |
|---|---|---|---|---|---|
| combo | 2 | 0.500 | 0.667 | 0.500 | 1.000 |
| **config** | 2 | **0.607** | 0.107 | 0.143 | 0.822 |
| **fault** | 13 | **0.884** | 0.448 | 0.915 | 0.929 |
| prompt | 3 | 0.900 | 0.867 | 0.900 | 0.967 |
| tool | 1 | 0.800 | 1.000 | 0.800 | 1.000 |

**The `fault` family — v1's worst weakness — went from 0.448 to 0.884, close to coverage's 0.915.**
Under family-level hold-out the model has never seen a tool-fault change, but lag and unit history
give it "this unit broke before" to work with. Even on `config`, where coverage cannot work at all
(`C(Δ)` is empty), it reaches 0.607.

### The one miss was a near-unattainable criterion

`recall_ratio_vs_random ≥ 2.0` demands an absolute recall of **0.933** while the in-budget ceiling is
**0.934**. **The gap is 0.001 — it asks for oracle performance.** The criteria were fixed before
implementation and are not changed, but the design was too demanding.

Written as an absolute (say, recall ≥ 0.80), this experiment would have passed. **A criterion of the
form "N× random" becomes automatically unattainable wherever random is strong** — recorded here as a
lesson in criterion design.

## 4. E3-9: neither confirmed nor refuted

Report 3.8 gives ε-exploration the role of "keeping the selector calibrated". The changes were
processed sequentially under partial feedback, comparing ε ∈ {0, 0.05, 0.2}.

**The first implementation tried only one ordering, and on it the calibration gain read +0.006.**
With five orderings and a **paired** comparison:

| Metric | Value | Criterion |
|---|---|---|
| `calibration_gain` (late-round Brier improvement) | **−0.0023** (paired SD 0.0088) | > 0 × |
| `escape_gain` | +0.0096 (SD 0.070) | ≥ 0 ○ |
| `exploration_cost` | 0.0487 | ≤ 0.10 ○ |
| `exploration_gain` (gain in rounds where exploring won) | 0.0576 | — |

**The effect vanished** — the single-ordering result was ordering variance. A power calculation puts
the requirement at **113 orderings** (407 for the escape-defect rate). So this did not show that
exploration has no effect; it showed that **any effect is smaller than five orderings can resolve**.

The cost of exploring stayed within the criterion, and on balance exploration is slightly ahead
(cost 0.049 against a gain of 0.058). As a side observation, **full observation's late Brier (0.088)
is worse than partial observation at ε=0 (0.068)** — the control meant as a ceiling was not one.

## 5. E3-10: lag works; what is missing is history length

The 65 changes were processed in **change-history order**, predicting change t using only records
with `seq < t` — **prequential** evaluation, where the future is structurally invisible.

| Metric | Value | Criterion |
|---|---|---|
| `prequential_auc` | 0.761 | ≥ 0.75 ○ |
| `recall_at_budget_30` | **0.810** | ≥ 0.60 ○ |
| `late_auc_gt_early_auc` (does learning accrue?) | **1** (early 0.752 → late 0.769) | == 1 ○ |
| `history_feature_gain` | −0.075 | > 0 × |
| `beats_coverage_baseline` | 0 (0.761 vs 0.776) | == 1 × |

**The temporal protocol improves selection substantially**: recall is 0.810 against 0.535 under
E3-8's family-level hold-out. Training on the past to predict the next change — the realistic
deployment — works far better than holding out a whole family.

### Ablation over history feature groups

| History group | Global AUC | Within-change AUC | Recall@30% |
|---|---|---|---|
| none | 0.836 | 0.853 | 0.763 |
| **lag only** | **0.878** | 0.855 | **0.816** |
| test history only | 0.822 | 0.857 | 0.763 |
| cross only | 0.815 | 0.856 | 0.763 |
| lag + cross | 0.819 | 0.856 | 0.769 |
| all | 0.747 | 0.854 | 0.810 |
| (coverage alone) | 0.776 | **0.922** | 0.816 |

**Lag alone is the best, and it beats coverage alone (0.776).** Recency — how many changes since this
test last ran or failed, since this unit was last changed — works exactly as industrial PTS treats it.

**But adding every group drops it to 0.747.** With only 122 positives, 17 correlated features
overfit. In this environment regressions are driven by *which unit the change touched*, not by test
fragility, so the test-side historical failure rate acts as a confounder — it makes the model suspect
the same test even when an unrelated unit changed.

### Group selection was delegated to inner validation, and it could not decide

**Picking `lag` after seeing the results would satisfy both failing criteria** (AUC 0.878 > coverage
0.776). That would be tuning to the criteria, so instead — under the same discipline as model
selection (ADR-025) — group choice was delegated to an **inner temporal split** (train on the first
70% of the prefix, validate on the last 30%).

The inner validation chose `lag` 15 times, `all` 14, `none` 11, `cross` 10, `test_history` 4,
`lag+cross` 1, giving AUC 0.761. Too few positives fall inside the inner window to tell the groups
apart. **What is needed is enough history to run inner validation on — several hundred synthetic
changes should resolve it.**

### Coverage still dominates within-change ranking

Within-change AUC is **0.922** for coverage alone against 0.855 for the learned model — the same
conclusion as E3-8: the dominant signal in this environment is report 3.2's dependency graph.

## 6. How to read chapter 3 as a whole

| § | Method | Experiment | Verdict | Point |
|---|---|---|---|---|
| 3.2 | Trajectory coverage dependency graph | E3-1 | PASS | All 11 flips in the candidate set; candidates are 34.8% |
| 3.3 | Semantic impact estimation | E3-2 | PASS | Conditionally (deterministic tag extraction) |
| 3.4 | Uncertainty-driven selection | E3-3 | NEGATIVE | `H(p̂)` cannot beat random |
| 3.4 | **Learned failure prediction** | **E3-8** | **FAIL** | **4 of 5 criteria met; the miss is near-unattainable** |
| 3.5 | Test-pyramid level decision | E3-4 | PASS | Conditionally |
| 3.6 | Prefix cache and branch re-execution | E3-5 | NEGATIVE | No savings |
| 3.7 | SPRT early stopping | E3-6 | PASS | 0.544× fixed-n |
| 3.8 | Escape rate, ε, inviolable set | E3-7 | NEGATIVE | Skeleton holds; selection quality misses |
| 3.8 | **Exploration and calibration** | **E3-9** | **NEGATIVE** | **Effect below measurement error** |
| 3.4/3.8 | **Prequential evaluation along history** | **E3-10** | **FAIL** | **Lag works (AUC 0.878); too little history for group selection** |

**Conclusions for chapter 3**

1. **PTS works — but only once change history and test history are recorded.** Without history (the
   original 8 changes, no lag features) learning lost to a simple dependency-graph rule. With the
   ledger, lag and churn, the escape-defect rate went from 0.868× random to **0.319×**, and recall at
   a 30% budget from 0.535 to **0.819**
2. **The feature that works is recency.** In the ablation, lag alone beats coverage alone (0.878 vs
   0.776). The test-side historical failure rate is a confounder here, because regressions are
   change-driven rather than test-driven
3. **The dependency graph (3.2) remains the strongest single signal**, especially for within-change
   ranking (AUC 0.922). It is worth keeping as a separate signal rather than dissolving it into the
   model
4. **Uncertainty `H(p̂)` (3.4) does not work as a selection criterion** (E3-3). The "keeps the
   selector calibrated" role 3.8 assigns it cannot be measured at this scale (E3-9)
5. **Scale decides everything.** 65 changes and 122 positives sit orders of magnitude below
   industrial PTS (hundreds of thousands of commits). Both family-level hold-out and inner validation
   are starved of history

## 7. Lessons in criterion design

The rebuild showed that **two of the criteria we set ourselves were unattainable or unmeasurable**.
Neither was changed — both were fixed before implementation — but they are recorded here.

| Criterion | Problem | How it surfaced |
|---|---|---|
| E3-8 `recall_ratio_vs_random ≥ 2.0` | Demands absolute recall 0.933 when the in-budget ceiling is 0.934 — **a gap of 0.001** | Computing the in-budget oracle |
| E3-9 `calibration_gain > 0` | The effect is buried in ordering variance (SD 0.009); 113 orderings would be needed | Going from 1 to 5 orderings |

**A criterion of the form "N× random" becomes automatically unattainable wherever random is strong.**
When setting a criterion, two habits are warranted: (1) compute the in-budget ceiling (oracle) first,
and (2) estimate the ratio of effect size to measurement error (power) first. This is the same point
as B5 in the previous review — the same class of oversight, twice running.

## 8. If there were a next step

1. **Scale the synthetic changes to several hundred.** Both E3-10's inner validation failing to pick
   a group and E3-8's family hold-out being too harsh reduce to insufficient history length
2. **Run E3-9 with 113 orderings** (the number the power calculation gives)
3. **Restate the fusion policy as a formal hypothesis** (turn E3-8's diagnostic into a new experiment
   with criteria fixed first)
4. **Diversify the tasks' tool sequences.** Sim trajectories collapse into 13 distinct tool-name
   sequences, which together with E3-3's redundancy problem affects every selection evaluation
5. **Confirm under live.** Synthetic changes act through the simulator's behaviour flags, so a real
   model need not produce the same regression patterns (the same concern as L6 in E0-1)
