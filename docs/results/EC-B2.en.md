# EC-B2 Context-cost attribution: lifetime cost of tool output and a prior prediction for history caching

*日本語版: [EC-B2.md](EC-B2.md)*

## The five items
- Hypothesis: The allocation matches the ledger, most of the extra cost of the verbose-output version is attributable to run_tests output, and the extra cost of turning history caching off can be predicted from trajectory token counts alone and matches the measurement.
- Planted condition: c04_verbose_ctx (test_output_limit disabled plus verbose test output) and c01o_no_history_cache (c01 with history_cache: true removed; a predictable cost increase). c04 runs on 24 tasks (core24) x 3 repeats (72 runs; ADR-044 (A); for H2 each task is paired with the mean of the 6 c01_baseline repeats on the same task); c01o runs on 10 tasks (sub10) x 1. The 'on' side of H3 is all c01_baseline corpus runs (core24 x 6).
- Control: A scripted-agent pair that emits the same verbose output at step 1 vs only at the last tool step (simulated, history cache off; it borrows the c01o_no_history_cache harness). c12_null is recorded only.
- Pass criteria: allocation_identity_error <= 1e-09, ledger_identity_error <= 0.001, verbose_attribution_to_run_tests >= 0.8, history_cache_prediction_error <= 0.1, control_attribution <= 0.1
- Provenance: live

## Method
From corpus-stage live runs, allocate costs by the position model and reconcile with the ledger (H1); compute the share of the c04 extra cost attributable to run_tests (H2); predict the extra cost of history caching off by a within-run counterfactual that does not use cache-related usage (H3); and check that the attribution reflects the number of resends with the scripted control pair (a). See docs/results/EC-B2.md.

## Results
| Metric | Value (provenance) | Criterion | OK |
|---|---|---|---|
| allocation_identity_error | 6.489e-16 [live] | <= 1e-09 | ○ |
| c04_minus_c01_mean_steps | {'estimate': -0.48611111111111116, 'low': -1.2848958333333333, 'high': 0.22239583333333204, 'n_tasks': 24} [live] | — | — |
| c12_minus_c01_run_tests_cca_usd | {'estimate': 6.31833687743756e-06, 'low': -0.00015354641273500496, 'high': 0.0001482520869800854} [live] | — | — |
| control_attribution | 0.09094 [simulated] | <= 0.1 | ○ |
| control_detail | {'early_run': 'cd__CT-tillcalc-004__c01o_no_history_cache__offline__r0__sc-b2-early', 'late_run': 'cd__CT-tillcalc-004__c01o_no_history_cache__offline__r0__sc-b2-late', 'ratio': 0.09093668929734505, 'predicted': 0.09090909090909091, 'early_steps': 13, 'late_steps': 13} [simulated] | — | — |
| e_on_c04 | -0.004719 [live] | — | — |
| e_on_e_off | {'e_on': -0.0058418556673769115, 'e_off': -1.198607114652519e-16, 'n_on': 144, 'n_off': 10} [live] | — | — |
| e_on_sub10_only | -0.004722 [live] | — | — |
| history_cache_inactive_on_runs | 0 [live] | — | — |
| history_cache_increase_between_versions | {'estimate': 1.1156129706522888, 'low': 0.7641599883117698, 'high': 1.5134410446300046} [live] | — | — |
| history_cache_predicted_increase_rate_sub10 | 1.518 [live] | — | — |
| history_cache_prediction_error | 0.005842 [live] | <= 0.1 | ○ |
| ledger_identity_error | 6.489e-16 [live] | <= 0.001 | ○ |
| ledger_identity_runs_checked | 274 [live] | — | — |
| ledger_identity_voided_reason | None [live] | — | — |
| ledger_match_rate | 1 [live] | — | — |
| off_runs_with_cache_beyond_prefix | 0 [live] | — | — |
| predicted_resend_ratio_n10 | 0.215 [simulated] | — | — |
| prefix_tokens | 8814 [live] | — | — |
| runs_fully_matched | 274 [live] | — | — |
| runs_total | 274 [live] | — | — |
| verbose_attribution_ci | {'low': -20.652077652207055, 'high': 23.040704365267374, 'n_tasks': 24} [live] | — | — |
| verbose_attribution_denominator_usd | {'estimate': 0.046462333333333355, 'low': -0.061488344166666604, 'high': 0.12873233791666666, 'n_tasks': 24} [live] | — | — |
| verbose_attribution_numerator_usd | {'estimate': 0.11161582117618049, 'low': 0.09547251230123807, 'high': 0.12928274972248016, 'n_tasks': 24} [live] | — | — |
| verbose_attribution_to_run_tests | 2.402 [live] | >= 0.8 | ○ |


## Verdict
PASS

Designer's verdict (W5-A3, after seeing the corpus; criteria and thresholds unchanged): PASS. All five criteria fixed in advance were met. H1: the allocation matched the ledger on 274/274 runs (error 6.5e-16 [live]); this checks the price-table copy, the row-to-step matching and the sum, not block-wise attribution. H3: the position model, without cache-related usage, predicted the extra cost of turning history caching off within 0.6% (history_cache_prediction_error 0.0058 [live]). H2 is not verified: verbose_attribution_to_run_tests met its criterion as a point estimate (2.40 >= 0.8), but its 95% interval [-20.7, 23.0] is undetermined because the denominator, the c04 - c01 cost increase, is 0.046 USD [-0.061, 0.129] and cannot be told apart from zero. What was verified is only that the numerator (the increase in run_tests-output cost) appeared at the size estimated in advance: 0.112 USD [0.095, 0.129] vs the prior estimate 0.121 USD. The PASS rests on the point-estimate criterion fixed by ADR-044 and must not be read as evidence that most of the c04 extra cost is due to run_tests output. It is neither FAIL (the identities hold and the numerator matches the estimate) nor NEGATIVE (the NEGATIVE scenario, extra steps driving the cost, did not occur: the step difference is -0.486 [-1.285, 0.222]). control_attribution 0.09094 vs the predicted 1/11 is fixed by arithmetic, not a borderline pass.

## Findings and limitations
- ledger_identity_error verifies only the price-table copy, the row-to-step matching and the component sum, not block-wise attribution (which tool result cost how much); the only block-level evidence is the position-model premise and control (a), as in EC-B2P. The identity was checked on 274 runs (with a ledger match rate below 0.95, or zero checked runs, the value is withheld and the experiment fails). allocation_identity_error is a trivially satisfied self-check and is not evidence for H1.
- Control (a) is simulated (synthetic usage, 1 token = 4 bytes) and almost determined by arithmetic. It rules out an implementation that attributes by output size alone (ignoring the number of resends), which would give a ratio of 1.0; the observed ratio is 0.09094 (position-model prediction 1/11 = 0.0909). It exercises only the history-cache-off path (the predicted ratio on the 'on' path is about 0.56, to which this criterion does not apply); the 'on' path is supported only at the run-total level by H3's e_on. Absolute amounts must not be compared with live values.
- H2: the c04 verbose output may change behaviour (step count), so the extra cost D_t includes non-resend increments and the ratio can exceed 1 or fall below 0.8; 0.8 is an operational definition of "most of the increase". Interval [-20.7, 23.0] (24 tasks). Recorded only [live, 24-task cluster bootstrap]: numerator verbose_attribution_numerator_usd 0.112 [0.095, 0.129], denominator verbose_attribution_denominator_usd 0.046 [-0.061, 0.129], c04_minus_c01_mean_steps -0.486 [-1.285, 0.222]. A ratio of sums is fragile when behavioural noise moves the denominator towards zero. Procedural gap: W3-A2 planned to estimate the ratio's interval width by the delta method and to propose a criterion such as "lower bound >= 0.6" by ADR if it exceeded 0.2; this was not done, and the criterion is not changed after seeing the data. Stage 2 should pre-compute the ratio's width with behavioural noise, compare the numerator with a prior prediction instead, or compare at equal step counts. Verbose output adds about 0.0047 USD per run (about 9% of a c01 run) with no change in bugfix success (c04 0.917, c01 0.910; not tested).
- H3's primary metric is the within-run counterfactual |e_off - e_on|, which does not use cache-related usage; most information is in e_on (e_on = -0.0058 over 144 runs, e_off = -1.2e-16 over 10 runs; e_on is -0.0047 on sub10 alone and -0.0047 for c04). A broken cache chain (history cache not read in an 'on' run, or cache beyond the prefix in an 'off' run) counts as an implementation error (FAIL); there were 0 of each. The between-version comparison (recorded only) came out lower than the prior prediction: history_cache_increase_between_versions = 1.116 [0.764, 1.513] [live], while the prior 1.586 (pilot trajectories) and 1.518 (corpus c01 sub10 trajectories) both lie above the interval; in off/on cost ratio, about 2.1x measured vs 2.5-2.6x predicted. This is consistent with c01o's shorter trajectories (mean 13.7 vs 14.5 steps) but the cause is not established. The prior prediction concerns the same trajectory sent under another setting, so this is not a refutation of the H3 criterion.
- The c04 and c01 tool definitions and prompts were checked to be identical (72/72 pairs) so the prefix P can be shared; per-version e_on is recorded for c04 only (c12 and c13 have a different system prompt). The c12 - c01 difference in run_tests attribution is recorded only: 6.3e-6 USD [-1.5e-4, 1.5e-4] [live], about 3% of c04's per-task increase; no equivalence margin was fixed in advance, so it is not read as a criterion.

---
Generated by `uv run agenteval exp ec_b2` / run at 2026-11-08T00:57:04+00:00 / cost 0.0 USD
