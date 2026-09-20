# Rebuilding and Verifying Chapter 3's PTS

*日本語版: [PTS.md](PTS.md)*

Date: 2026-09-20 / Scope: report sections 3.2–3.8 (Predictive Test Selection)

E3-3 produced a negative result: selecting by uncertainty `H(p̂)` cannot beat random. But **PTS is a
mechanism with demonstrated industrial value**, so it mattered whether that negative was a limit of
the method or a flaw in how we built it. This is the record of the rebuild.

## 0. Outcome

| | Before | After |
|---|---|---|
| PASS | 15 | 15 |
| NEGATIVE | 5 | **6** |
| FAIL | 7 | **8** |
| Experiments | 27 | **29** |

**Report 3.4 makes two claims at once. Separating them makes clear which half holds.**

| Claim | Experiment | Result |
|---|---|---|
| (a) `p̂_t` estimated from features is usable for selection | **E3-8** (new) | **Prediction works** (AUC 0.826); selection recall misses the bar |
| (b) Selecting the highest-uncertainty tests is good | E3-3 | **Does not hold** (worse than random) |
| (c) ε-exploration keeps the selector calibrated (3.8) | **E3-9** (new) | **Neither confirmed nor refuted** (effect below measurement error) |

E3-3's hypothesis and criteria were **not rewritten** (ADR-025). Rewriting a failed hypothesis is
"tweaking until it matches"; the correct move is to state the decomposed claims as new experiments.

---

## 1. The root cause: there was no training data

Report 3.8 **spells out the cold-start procedure**:

> When there is no training data (change → outcome flip), create flip data by deliberately
> introducing synthetic changes such as prompt perturbations and model swaps. (report 3.8)

**The project had never done this.** There were only 8 change events, and measured:

| | Before | After |
|---|---|---|
| Change events | 8 | **37** (synthetic) |
| ...that produce regressions | 3 | **18** |
| Training rows | 368 | **1,702** |
| Positives (regressions) | — | **108** (4.2%) |

Verifying "supervised failure prediction" was impossible when 5 of 8 changes produced no
regressions at all. `pts/synthetic.py` mechanically builds 37 changes by perturbing v01_baseline,
and 46 tasks × 5 repeats gives an 8,740-run corpus in `data/pts_corpus/` (ADR-024).

Breakdown: 13 prompt-section additions/removals, 7 config, 1 tool schema, 1 model swap,
**12 tool faults**, 3 two-factor.

## 2. Four defects found during the rebuild

### D1 [critical] Fault injection executed the tool and only then replaced the response → ADR-026

`_execute_tool` ran the tool **against the environment first**, then passed the response to
`FaultInjector`. Since `fault="error"` only swaps the response, **the write succeeded while
"failing"**.

With a fault on `calendar_create`, the agent received an error and behaved as if nothing was
created — yet the acceptance check `event_exists(...)` looked at the DB and returned **True**.

| Faulted tool | Kind | Regressed tasks (before → after) |
|---|---|---|
| calendar_search / mail_search / file_read / external_lookup | read | 11 / 4 / 5 / 5 → unchanged |
| calendar_create | write | **0 → 10** |
| mail_send | write | **0 → 11** |
| file_write | write | **0 → 9** |
| calendar_delete / file_delete | write | 0 → 1 / 2 |

It surfaced because **all six write-tool faults were cleanly zero**. Looking only at whether
experiments pass would never have found it. `timeout` / `error` now return an error *without*
executing; `partial` / `schema_change` (which mutate a successful response) still apply afterwards.

### D2 [critical] Config changes were indistinguishable to the model

`max_steps=3` and `max_steps=6` had **identical feature vectors**. The only change-side features
were `change_kind_config` and `n_components`, so config changes were unlearnable in principle.
Change × test cross features were added (`max_steps_headroom = max_steps − the test's mean steps`,
and similar).

### D3 [critical] The model was hard-coded

The first implementation pinned `best = results["gbdt"]`. GBDT overfits 108 positives (AUC 0.638)
while logistic reaches 0.759 — and picking "the better one" at that point would be choosing after
seeing the result. It now uses **nested cross-validation that compares candidates inside the
training folds only** (never looking at the held-out score). The inner CV chose logistic in every
fold.

### D4 [critical] Evaluation and training operated on different units → ADR-027

Evaluation used global AUC, but **test selection happens per change**. When picking the top k for
one change, only the ranking *within that change* matters.

A structural consequence (true regardless of the verdict): **a feature that is constant within a
change cannot contribute to ranking within that change** — adding a constant to a score leaves the
order unchanged. `change_kind_*`, `max_steps` and `n_components` are exactly such features.

| Model | Global AUC | Within-change AUC |
|---|---|---|
| Coverage overlap only | 0.793 | **0.858** |
| Logistic (pre-revision) | 0.759 | 0.730 |
| GBDT | 0.638 | 0.624 |

The two moved in **opposite directions**. Adding within-change percentile ranks for the continuous
cross features lifted global AUC from 0.747 to **0.826**.

---

## 3. E3-8: prediction works, selection falls short

### Learning does work

| Metric | Value | Criterion |
|---|---|---|
| ROC AUC (family-level leave-one-group-out) | **0.826** | ≥ 0.75 ○ |
| ROC AUC (single-change hold-out, diagnostic) | **0.842** | — |
| Within-change AUC | 0.761 | — |
| Coverage-only AUC | 0.793 | — |
| Historical failure rate only (change-blind control) | **0.553** | — |

The change-blind control sits near chance, confirming that **the change information is what makes
prediction possible**. Under the realistic single-change hold-out, learning (0.842) beats coverage
alone (0.793).

Top feature contributions:

| Feature | Contribution |
|---|---|
| `hist_regress_rate_same_kind` (past regression rate for this kind of change) | 0.096 |
| `plan_first` | 0.062 |
| `coverage_overlap_rank_in_change` | 0.059 |
| `max_steps` / `max_steps_headroom` | 0.057 |
| `summarize_headroom_rank_in_change` | 0.051 |

"Flip count" and "coverage overlap" from report 3.4's table do land near the top.

### Selection recall missed

Recall of regressions at a 30% budget (in-budget ceiling 0.944):

| Policy | Recall | Escape-defect rate |
|---|---|---|
| Learned model | 0.535 | 0.462 |
| Coverage overlap only | **0.781** | — |
| Equal-weight rank fusion (diagnostic) | 0.739 | **0.270** |
| Uncertainty `H(p̂)` | 0.478 | — |
| Historical failure rate only | 0.477 | — |
| Random | 0.463 | 0.532 |

**The family breakdown explains it.**

| Family | Changes | Learned | Coverage | Fusion | Random | Ceiling |
|---|---|---|---|---|---|---|
| combo | 2 | 0.667 | 0.500 | **1.000** | 0.683 | 1.000 |
| config | 2 | 0.107 | 0.143 | **0.607** | 0.303 | 0.822 |
| **fault** | **10** | **0.448** | **0.926** | 0.729 | 0.445 | 0.945 |
| prompt | 3 | 0.867 | 0.900 | 0.767 | 0.534 | 0.967 |
| tool | 1 | **1.000** | 0.800 | 0.500 | 0.312 | 1.000 |

**The learned model matches or beats coverage on every family except `fault`, where it loses
badly.** Under family-level hold-out it has never seen a tool-fault change, so it learns how much
to trust coverage **from the kinds of change where coverage is weak**. Ten of the 18 evaluable
changes are faults, so the average follows them.

Conversely, coverage cannot work at all on `config` (a config change has an empty `C(Δ)`), scoring
0.143. The learned model is also low at 0.107 — but **fusion reaches 0.607**.

### Checking whether the criteria were attainable

`recall_ratio_vs_random ≥ 2.0` demands an absolute recall of **0.926**, while the in-budget ceiling
is only **0.944**. **That criterion asked for near-oracle performance.** The criteria were fixed
before implementation and are not changed, but the criterion design was too demanding and that is
recorded. Random already catches 46% of regressions at a 30% budget, because that budget buys 19 of
46 tasks.

### Practical implication

Fusing the learned score and the coverage score by **within-change percentile ranks with equal,
untuned weights** drops the escape-defect rate from 0.462 to **0.270** (0.507× random).

> **Keep the dependency graph (report 3.2) as a separate signal and fuse it, rather than dissolving
> it into the model.** It is the insurance policy for a kind of change the model has never seen.

This policy was added after seeing the family breakdown, so it is not used for the verdict — but it
is directly usable in practice.

## 4. E3-9: neither confirmed nor refuted

Report 3.8 gives ε-exploration the role of "keeping the selector calibrated". The 37 changes were
processed sequentially under partial feedback (only the selected tests' results are observed),
comparing ε ∈ {0, 0.05, 0.2}.

**The first implementation tried only one ordering, and on that ordering the calibration gain read
+0.006.** With five orderings and a **paired** comparison (the same ordering for ε=0 and ε=0.05):

| Metric | Value | Criterion |
|---|---|---|
| `calibration_gain` (late-round Brier improvement) | **−0.0023** (paired SD 0.0088) | > 0 × |
| `escape_gain` (late-round escape improvement) | +0.0096 (SD 0.070) | ≥ 0 ○ |
| `exploration_cost` | 0.0487 | ≤ 0.10 ○ |
| `exploration_gain` (gain in rounds where exploring won) | 0.0576 | — |

**The effect vanished. The single-ordering result was picking up ordering variance.**

A power calculation says detecting an effect of this size at 80% power needs **113 orderings**
(**407** for the escape-defect rate). Five is nowhere near enough. So this experiment did not show
that exploration has no effect — it showed that **any effect is smaller than five orderings can
resolve**.

That said, **the cost of exploring stayed within the criterion**, and on balance exploration is
slightly ahead (cost 0.049 against a gain of 0.058; late-round recall goes 0.736 → 0.757). The
worry that "exploration sacrifices a lot of current-round performance" is refuted.

As a side observation, **full observation's late-round Brier is 0.088 — worse than partial
observation at ε=0 (0.068)**. More data does not always calibrate better: full observation feeds in
a large number of easy negatives, which in this low-positive setting shrinks the predicted
probabilities. The control intended as a ceiling did not act as one.

## 5. How to read chapter 3 as a whole

| § | Method | Experiment | Verdict | Point |
|---|---|---|---|---|
| 3.2 | Trajectory coverage dependency graph | E3-1 | PASS | All 11 flips in the candidate set; candidates are 34.8% |
| 3.3 | Semantic impact estimation | E3-2 | PASS | Conditionally (deterministic tag extraction) |
| 3.4 | Uncertainty-driven selection | E3-3 | NEGATIVE | `H(p̂)` cannot beat random |
| 3.4 | **Learned failure prediction** | **E3-8** | **FAIL** | **Prediction works (AUC 0.826); selection recall misses** |
| 3.5 | Test-pyramid level decision | E3-4 | PASS | Conditionally |
| 3.6 | Prefix cache and branch re-execution | E3-5 | NEGATIVE | No savings |
| 3.7 | SPRT early stopping | E3-6 | PASS | 0.544× fixed-n |
| 3.8 | Escape rate, ε, inviolable set | E3-7 | NEGATIVE | Skeleton holds; selection quality misses |
| 3.8 | **Exploration and calibration** | **E3-9** | **NEGATIVE** | **Effect below measurement error** |

**Conclusion for chapter 3**: what reliably works in this environment is **3.2 (the dependency
graph)**. 3.4's uncertainty does not work as a selection criterion (E3-3), and supervised learning
works as a *predictor* but cannot surpass the dependency graph (E3-8). The scale (37 changes, 108
positives) is orders of magnitude below industrial PTS (hundreds of thousands of commits), and that
gap is what shows.

## 6. If there were a next step

1. **Scale the synthetic changes to several hundred.** Family-level hold-out is harsh here because
   each family has only 1–13 changes. With many changes per family the "unseen kind" setting
   relaxes and the conditions approach industrial PTS
2. **Run E3-9 with 100 orderings** — the power calculation says 113
3. **Restate the fusion policy as a formal hypothesis** (turn E3-8's diagnostic into a new
   experiment with criteria fixed first)
4. **Diversify the tasks' tool sequences.** Same root cause as E3-3's redundancy problem: sim
   trajectories collapse into 13 distinct tool-name sequences, which affects every selection
   evaluation
5. **Confirm under live.** Synthetic changes act through the simulator's behaviour flags, so a real
   model need not produce the same regression patterns (the same concern as L6 in E0-1)
