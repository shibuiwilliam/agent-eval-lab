# Experiment Catalogue

*日本語版: [02-experiment-catalog.md](02-experiment-catalog.md)*

Every experiment carries five items: hypothesis, planted condition, control, pass criteria, provenance. `experiments/registry.yaml` is the machine-readable transcription of this catalogue.
"Flake band" = the spread of pass/fail outcomes across 3 runs of the baseline v01. A change inside that band does not count as a change.

---

## Chapter 3 — Predictive Test Selection

### E3-1 Trajectory coverage dependency graph (3.2)
- Hypothesis: every test that actually flipped under a tool change is contained in the candidate set `T_cand(Δ)`, and the candidate set is meaningfully smaller than the full suite
- Planted: v06_toolschema_v2 (argument change in `calendar_search`). Flips are measured from v01 → v06 in the corpus
- Control: a synthetic change `Change(kind=tool, components={mail_send})`. A test that never calls `mail_send` must not enter the candidate set
- Criteria: recall_of_flipped = 1.0 (after excluding flakes), candidate_ratio ≤ 0.6, zero non-applicable tests among the control candidates
- Provenance: replay

### E3-2 Semantic impact estimation (3.3)
- Hypothesis: impact tags enumerated by an LLM from a prompt diff select the date-related tasks and capture the flipped tests with high recall
- Planted: v02_dateformat (a change to the date-format instruction)
- Control: a diff that changes only tone, `v02b_tone` (no runs needed — the diff alone is used). Impact tags must select at most 20% of the suite
- Criteria: recall_of_flipped ≥ 0.8, control selected_ratio ≤ 0.2, precision higher than coverage alone
- Provenance: live (Haiku, on the order of 10 calls) + replay

### E3-3 Uncertainty-driven selection (3.4)
- Hypothesis: at a 30% budget, selection based on `H(p̂)/c` yields a lower escape-defect rate than random selection and than most-recently-failed, and `expected_escape` is calibrated against the measured value
- Planted: leave-one-change-out evaluation over each of the changes v02–v09
- Control: random selection at the same budget (averaged over 1,000 draws), and most-recently-failed
- Criteria: escape(uncertainty) ≤ 0.5 × escape(random), |expected_escape − actual| ≤ 0.15
- Provenance: replay + simulated

### E3-4 Test-pyramid level decision (3.5)
- Hypothesis: the level decided from the change classification is at least the smallest level at which a flip is observed. A change invisible to the LLM stops at L1 and produces no flips
- Planted: v02 (prompt → L2 or above), v06 (tool → L3 or above), v09 (model → L4)
- Control: `Change(kind=code)` (a refactor of the tool wrappers; the version hash is unchanged). It must stop at L0–L1 with zero non-flake flips
- Criteria: decided_level ≥ observed_min_level for every planted case, control non-flake flips = 0
- Provenance: replay

### E3-5 Prefix cache and branch re-execution (3.6)
- Hypothesis: branch re-execution agrees with normal execution on outcomes, and for small changes it greatly reduces live steps. The larger the change, the earlier it diverges
- Planted: branch-re-execute v02 (a small change) and v09 (a model swap) from v01's trajectories
- Control: self-branching v01 → v01. Replay hits, so it never diverges and uses 0 live steps
- Criteria: outcome agreement ≥ 0.8 (20 tasks), live_step_ratio(v02) ≤ 0.6, median divergence(v09) < median divergence(v02), self-branch live steps = 0
- Provenance: live

### E3-6 SPRT early stopping (3.7)
- Hypothesis: SPRT achieves error rates close to the nominal α = 0.05 and β = 0.10 while settling in fewer trials than a fixed-n procedure
- Planted: Monte Carlo over `p_true ∈ {0.1, 0.3, 0.5, 0.7, 0.9}` (10,000 trials each). Live uses 5 boundary tests
- Control: a fixed-n procedure with the same power
- Criteria: empirical α ≤ 0.07, β ≤ 0.12, mean trials ≤ 60% of fixed-n, live boundary tests settle in ≤ 8 runs on average
- Provenance: simulated + live (small)

### E3-7 Escape-defect rate, ε-exploration, inviolable set (3.8)
- Hypothesis: at a 40% budget the escape-defect rate is ≤ 0.1. With ε = 0.05 a random test is mixed in every time. The inviolable set is always selected regardless of budget
- Planted: comparison between full-execution results and selection results across all changes
- Control: a 100% budget → escape-defect rate 0
- Criteria: escape ≤ 0.1, ≥ 1 random test mixed in per draw, inviolable selection rate = 100% (even at a 5% budget)
- Provenance: replay

### E3-8 Test selection from a learned failure model (estimating 3.4's `p̂_t` supervised)
- Hypothesis: learning **the probability that this test regresses under this change** from
  change × test features catches regressions at a higher recall than random selection at the same
  budget. Report 3.4's feature table is effective
- Planted: 37 synthetic changes built per report 3.8's "cold start" (13 prompt-section
  additions/removals / 7 config / 1 tool schema / 1 model swap / 12 tool faults / 3 two-factor).
  Each change × 46 tasks × 5 repeats is executed in sim into `data/pts_corpus/` (kept separate
  from the existing corpus)
- Control: (a) random selection, (b) baseline failure rate only (blind to the change),
  (c) coverage overlap only, (d) uncertainty `H(p̂)` (the policy E3-3 tested). All run under the
  same budget and the same inviolable-set constraint
- Criteria:
  - `roc_auc` ≥ 0.75 (on changes held out by **leave-one-group-out over change families**)
  - `recall_at_budget_30` ≥ 0.80 (catch 80% of regressions at a 30% budget)
  - `recall_ratio_vs_random` ≥ 2.0
  - `escape_ratio_vs_random` ≤ 0.5 (report 3.8's KPI measured on regressions; the same bar as E3-3)
  - `control_random_recall` ≤ 0.6 (confirming the task is not trivially easy)
- Provenance: simulated

### E3-9 Uncertainty's role is exploration (separating 3.4 from 3.8)
- Hypothesis: selecting by `H(p̂)` does not reduce *this round's* escape-defect rate; it exists to
  **keep the selector calibrated** (the role report 3.8 gives ε-exploration). Under partial
  feedback, mixing in exploration improves late-round calibration and escape
- Planted: the 37 synthetic changes processed sequentially in a family-interleaved order, updating
  the model with **only the results of the tests that were selected**. Exploration ratios
  ε ∈ {0.0, 0.05, 0.2}, with the exploration slot filled either by top-`H(p̂)` or at random
- Control: ε = 0 (pure exploitation). Full observation is reported as the performance ceiling
- Criteria:
  - `calibration_gain` > 0 (late-round Brier at ε = 0.05 is lower than at ε = 0)
  - `escape_gain` ≥ 0 (late-round escape-defect rate at ε = 0.05 is no worse than at ε = 0)
  - `exploration_cost` ≤ 0.10 (the recall given up in the current round stays within 10 points)
- Provenance: simulated

### E3-10 Prequential evaluation along the change history, and the learning curve
- Hypothesis: **recording change history and test history as a ledger** and feeding in recency
  (how many changes since this test last ran / last failed, since this unit was last changed) and
  change size (lines changed, units touched) makes a realistic sequential deployment work — where
  only past records are available to predict the next change — such that (a) prediction improves as
  history accumulates and (b) it surpasses the dependency graph alone
- Planted: 65 synthetic changes ordered by `seq`. The set is built so that the same unit is changed
  2–7 times (drop / truncate / emphasize per section, four `max_steps` values, tool faults in an
  "always" and a "from step 2" variant), so the lag features take real values. Predicting change t
  may use **only records with `seq < t`** — the future is structurally invisible
- Control: (a) coverage overlap only, (b) **the same model with the history features removed**
  (ablation), (c) random selection. All at the same budget and inviolable-set constraint
- Criteria:
  - `prequential_auc` ≥ 0.75 (AUC when training only on the past and predicting the next change)
  - `history_feature_gain` > 0 (AUC with history/lag features minus AUC without)
  - `late_auc_gt_early_auc` == 1 (later changes score higher than earlier ones — learning accrues)
  - `recall_at_budget_30` ≥ 0.60 (regression recall under the temporal protocol)
  - `beats_coverage_baseline` == 1 (prequential AUC exceeds coverage alone)
- Provenance: simulated

---

## Chapter 4 — Process and plan evaluation

### E4-1 Trajectory metrics (4.2)
- Hypothesis: a planted version moves only the metric it corresponds to
- Planted: v04_loopy (duplicate-call rate and backtracking ↑), v03_noverify (verification rate ↓)
- Control: 3 repeats of v01 (small metric variance), and v02 (these metrics must not move)
- Criteria: dup_rate(v04) ≥ 2 × v01, verification_rate(v03) ≤ 0.5 × v01, v02's change stays inside the flake band
- Provenance: replay

### E4-2 Ablation-based wasted-call detection (4.3)
- Hypothesis: the wasted-call rate `U` is higher for v04 than for v01. A judge-based surrogate metric agrees well enough with ablation
- Planted: 5 runs of v04 and 5 of v01 (every call ablated)
- Control: a call whose result is used by the immediately following action (search → create) must be judged "useful"
- Criteria: U(v04) − U(v01) ≥ 0.15, surrogate precision / recall ≥ 0.7, control useful-rate ≥ 0.9
- Provenance: live

### E4-3 Trajectory linter (4.4)
- Hypothesis: violations of ordering and prohibition constraints are detected deterministically, with no false positives on the baseline
- Planted: v05_unsafe_delete (`no_delete_without_backup`), v03_noverify (`claim_done_requires_checks`)
- Control: v01. Plus hand-written event sequences and hypothesis-based property tests
- Criteria: detection rate ≥ 0.8 on planted versions (among runs containing the relevant action), v01 violation rate ≤ 0.1, all operator unit tests pass
- Provenance: replay + unit

### E4-4 Partial-order milestones (4.5)
- Hypothesis: partial credit is consistent with the quality ordering of versions, and awards full marks to legitimate paths that differ only in order
- Planted: the v01 / v03 / v05 corpora, plus synthetic trajectories with different orderings (implementation-first / test-first)
- Control: a synthetic trajectory whose arrival order violates the constraints must be penalised
- Criteria: Spearman ρ ≥ 0.6 between per-version milestone_score and acceptance pass rate; synthetic trajectories judged as expected
- Provenance: replay + unit

### E4-5 Step-level judging (4.6)
- Hypothesis: a step whose action has been mutated scores lower than the original under both rubric judging and reference-policy agreement
- Planted: 30 steps extracted from healthy v01 runs, each replaced with a wrong tool / wrong argument / destructive operation
- Control: judge the unmutated steps twice to measure stability
- Criteria: degradation rate on mutated steps ≥ 0.8 (both methods), mean score of the original steps ≥ 3.5/5, re-judge agreement (±1) ≥ 0.8
- Provenance: live (Sonnet, roughly 200 calls)

### E4-6 Plan externalisation and deviation rate (4.7)
- Hypothesis: forcing the plan out as structured output makes DAG checking work, and a version that ignores its plan has a high deviation rate
- Planted: v12_ignore_plan (instructed to emit a plan and then ignore it)
- Control: v01p (v01 with `plan_first: true`)
- Criteria: valid-DAG rate of v01p ≥ 0.9, δ(v12) ≥ δ(v01p) + 0.2, the deviation-justification judge rules the planted unjustified deviations unjustified at ≥ 0.7
- Provenance: replay + live (small)

### E4-7 In-trajectory NIAH (4.9)
- Hypothesis: recall R(n) falls as noise n rises, and the summarising strategy (v08) has a higher AUC than raw (v01)
- Planted: 4 niah task types × n ∈ {0, 2, 4, 8} × 3 repeats
- Control: at n = 0 both versions have high recall
- Criteria: R(0) ≥ 0.8 (both versions), R(8) ≤ R(0) − 0.2 (v01), AUC(v08) ≥ AUC(v01) + 0.1 (record as NEGATIVE if it does not hold — section 4.11 of the report itself notes that summarising can drop information)
- Provenance: live

### E4-8 pass@k / pass^k and consistency (4.10)
- Hypothesis: on boundary tasks the gap between pass@k and pass^k is large, making pass^k necessary as a reliability metric. The loopy version has higher step-count variance
- Planted: 5 repeats × 10 tasks of v01, and 3 repeats of v04
- Control: on trivial tasks pass@3 ≈ pass^3
- Criteria: pass@3 − pass^3 ≥ 0.2 on boundary tasks, gap ≤ 0.05 on trivial tasks, Var(steps, v04) > Var(steps, v01)
- Provenance: replay

### E4-9 Cost budgets and Goodhart pairs (4.11)
- Hypothesis: the step-minimising version improves the efficiency metric but degrades its paired verification-rate metric. A p95 budget rejects only the loopy version
- Planted: v07_step_minimizer, v04_loopy
- Control: v01
- Criteria: steps(v07) ≤ 0.8 × v01 **and** verification_rate(v07) ≤ 0.7 × v01; a p95 overrun is detected for v04 and not for v01
- Provenance: replay

---

## Chapter 8 — Countermeasures against test obsolescence

### E8-1 Mock fidelity and TTL (8.5)
- Hypothesis: when the external service changes, `F` drops and the mismatching keys are identified. Expired cassettes are flagged
- Planted: `DriftInjector.bump_external(v2)` (`price` → `unit_price`, plus value changes)
- Control: before the change, F = 1.0
- Criteria: F(after) ≤ 0.5, the mismatching key set == the changed key set, TTL-overrun detection rate = 100%
- Provenance: replay (no LLM)

### E8-2 Freshness and distribution distance (8.3)
- Hypothesis: JS divergence captures the shift in the production distribution, and MMD captures co-adaptive rephrasing
- Planted: day-by-day shift in `ProdStream` (new-category share 0% → 40%), plus Haiku rephrasing prompts into "how a familiar user would say it"
- Control: a resample from the same distribution
- Criteria: JS increases monotonically with the number of days, an alarm fires at ≥ 25% new categories, MMD p < 0.05 (rephrasing) and p ≥ 0.05 on the control in ≥ 90% of bootstrap draws
- Provenance: simulated + live (roughly 50 calls for rephrasing)

### E8-3 Discriminative power and saturation (8.4)
- Hypothesis: trivial tasks are judged saturated, while boundary tasks have high discriminative power
- Planted: trivial tasks T-901, T-902 (pass under every version)
- Control: boundary tasks (v01 pass rate 0.3–0.7)
- Criteria: |d(T-9xx)| < 0.1 and judged saturated; d ≥ 0.3 on boundary tasks; the saturation judgement reaches `lifecycle`
- Provenance: replay

### E8-4 Dual-track κ (8.6)
- Hypothesis: the mock-vs-live outcome agreement κ falls when drift is injected
- Planted: after `bump_external(v2)`, re-run live the 20 tasks that use `external_lookup`
- Control: κ before injection
- Criteria: κ(before) ≥ 0.6, κ(before) − κ(after) ≥ 0.3
- Provenance: live

### E8-5 Drift attribution (8.8)
- Hypothesis: factor-wise swapping identifies the factor with the largest main effect, and it matches the planted cause
- Planted: 3 single-factor changes (model: v09, prompt: v02, tool: v06) and 1 two-factor change
- Control: no change (all main effects inside the flake band)
- Criteria: ≥ 2 of the 3 single-factor cases match, the top 2 match for the two-factor case, no alarm under no change
- Provenance: live (branch re-execution, 8 combinations × 10 tasks)

### E8-6 Model fingerprinting (8.8)
- Hypothesis: comparing distributions over fixed probes raises no alarm when the same model is re-sampled, and raises one when the model is swapped
- Planted: swapping Haiku → Sonnet (a stand-in for a silent update)
- Control: sampling Haiku twice
- Criteria: control false-alarm rate ≤ 0.1 (bootstrap), swap detected at p < 0.05
- Provenance: live (roughly 150 calls)

### E8-7 Production→test pipeline (8.2)
- Hypothesis: the stratified sampler prioritises important sessions, the judge-drafted acceptance criteria are accepted by a human (or auto-approval), and registering them shrinks the distance between the suite and production
- Planted: 20 sessions drawn from 3 days of `ProdStream` sessions (live)
- Control: uniform sampling
- Criteria: the capture rate for failed / high-cost / new-category sessions is at least 3× uniform, draft approval rate ≥ 0.7, JS after registration is smaller than before
- Provenance: live (synthetic prod) + roughly 30 Sonnet calls

### E8-8 Lifecycle state machine (8.10)
- Hypothesis: the transition conditions act deterministically on metrics, and Retired is terminal
- Planted: unit tests supplying metrics that trigger each transition
- Control: metrics that do not meet a condition cause no transition
- Criteria: all transition unit tests pass, a transition out of Retired raises, `lifecycle.jsonl` is append-only
- Provenance: unit

### E8-9 Contamination detection (8.9)
- Hypothesis: canary scanning detects the contaminated version, and the public/private pass-rate gap widens for it
- Planted: v10_contaminated (canary-bearing examples from private tasks mixed into the few-shot block)
- Control: v01
- Criteria: scan detection rate = 100% (v10) and 0 for v01; gap(v10) ≥ 0.2 and gap(v01) ≤ 0.1
- Provenance: replay

---

## Extra versions needed (in addition to the initial 9)

| Version | Purpose |
|---|---|
| v02b_tone | Control for E3-2 (diff only, no runs needed) |
| v01p | Control for E4-6 (v01 + `plan_first: true`) |
| v10_contaminated | Planted condition for E8-9 |
| v12_ignore_plan | Planted condition for E4-6 |
