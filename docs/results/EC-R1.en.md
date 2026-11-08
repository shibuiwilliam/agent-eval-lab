# EC-R1 Three-class decomposition and decision-robust ROI (core)

*日本語版: [EC-R1.md](EC-R1.md)*

## The five items
- Hypothesis: c13 is no worse than c01 in visible pass rate but lower in net value (NV) measured against hidden tests, has a higher silent-wrong rate, and the decision holds over most of the assumed parameter range.
- Planted condition: c13_minimal_fix (version pinned to U1, ADR-046). The operation check failed all three times (specialization 0/10 each), so the mechanism is not specialization but local workarounds and instruction violations. Corpus: 24 tasks x 6 repeats. The parameter range Theta is docs/plan/coding/THETA.md.
- Control: c12_null (one irrelevant paragraph added), 24 tasks x 2 repeats: the data should rarely let us decide confidently between c01 and c12 at any theta.
- Pass criteria: w_rate_diff_ci_low > 0, visible_vs_nv_rank_reversal == 1, rob_c01_over_c13 >= 0.9, null_confident_fraction_c12 <= 0.05
- Provenance: live

## Method
Classify corpus-stage live runs into S / W / F, build per-version economics, and measure decision robustness with Rob over the parameter space (simulated) and simultaneous confidence bands. Point estimates and bootstrap replicates use the tasks common to the two versions compared. See docs/results/EC-R1.md.

## Results
| Metric | Value (provenance) | Criterion | OK |
|---|---|---|---|
| breakeven_pi_s | {'c01_baseline': {'defined': 0.794705, 'always_gain': 0.14625, 'always_loss': 0.059045, 'undefined': 0.0, 'quantiles': {'q05': 0.11099325714919671, 'q25': 0.44306215882669236, 'q50': 0.7472676648737738, 'q75': 0.9195028759488837, 'q95': 0.9878436779325208}}, 'c13_minimal_fix': {'defined': 0.7909, 'always_gain': 0.14794, 'always_loss': 0.06116, 'undefined': 0.0, 'quantiles': {'q05': 0.10903950935082624, 'q25': 0.43244910329759284, 'q50': 0.7372729368158284, 'q75': 0.9152765381099105, 'q95': 0.9872295894230974}}} [simulated] | — | — |
| class_counts | {'c01_baseline': {'S': 131, 'W': 11, 'F': 2}, 'c13_minimal_fix': {'S': 101, 'W': 34, 'F': 9}, 'c12_null': {'S': 45, 'W': 3, 'F': 0}} [live] | — | — |
| confident_fraction_c01_over_c13 | 0.9967 [simulated] | — | — |
| economics | {'c01_baseline': {'version_id': 'c01_baseline', 'n_runs': 144, 'pi_S': 0.9097222222222222, 'pi_W': 0.07638888888888888, 'pi_F': 0.013888888888888888, 'cost_usd': 0.049693618055555555, 'diff_lines_S': 2.2595419847328246, 'diff_lines_W': 5.181818181818182}, 'c13_minimal_fix': {'version_id': 'c13_minimal_fix', 'n_runs': 144, 'pi_S': 0.7013888888888888, 'pi_W': 0.23611111111111113, 'pi_F': 0.0625, 'cost_usd': 0.047615743055555555, 'diff_lines_S': 3.0396039603960396, 'diff_lines_W': 5.647058823529412}, 'c01_baseline_for_c12_null': {'version_id': 'c01_baseline', 'n_runs': 144, 'pi_S': 0.9097222222222222, 'pi_W': 0.07638888888888888, 'pi_F': 0.013888888888888888, 'cost_usd': 0.049693618055555555, 'diff_lines_S': 2.2595419847328246, 'diff_lines_W': 5.181818181818182}, 'c12_null': {'version_id': 'c12_null', 'n_runs': 48, 'pi_S': 0.9375, 'pi_W': 0.0625, 'pi_F': 0.0, 'cost_usd': 0.04896129166666666, 'diff_lines_S': 2.1333333333333333, 'diff_lines_W': 4.333333333333333}} [live] | — | — |
| hidden_test_mutation_score_mean | 0.8459 [simulated] | — | — |
| hidden_test_mutation_score_min | 0.7227 [simulated] | — | — |
| n_common_tasks | {'c01_c13': 24, 'c01_c12': 24} [live] | — | — |
| n_theta | 2e+05 [simulated] | — | — |
| null_confident_fraction_c12 | 0.00067 [simulated] | <= 0.05 | ○ |
| rob_by_theta_variant | {'narrow': 1.0, 'wide': 0.9859, 'linear': 1.0} [simulated] | — | — |
| rob_c01_over_c13 | 1 [simulated] | >= 0.9 | ○ |
| rob_c01_over_c13_boot_q05 | 0.9999 [simulated] | — | — |
| rob_c12_vs_c01 | 1 [simulated] | — | — |
| rob_conditional_on_parameter_tercile | {'H': [1.0, 1.0, 1.0], 'kappa': [1.0, 1.0, 1.0], 'R0': [1.0, 1.0, 1.0], 'rho': [1.0, 1.0, 1.0], 'R_F': [1.0, 1.0, 1.0], 'D': [1.0, 1.0, 1.0], 'miss': [1.0, 1.0, 1.0]} [simulated] | — | — |
| test_files_modified | {'c01_baseline': 0, 'c13_minimal_fix': 0, 'c12_null': 0} [live] | — | — |
| vis_fail_hidden_pass | 0 [live] | — | — |
| visible_rate_diff | {'estimate': -0.048611111111111105, 'low': -0.13194444444444445, 'high': 0.006944444444444443} [live] | — | — |
| visible_vs_nv_rank_reversal | 1 [simulated] | == 1 | ○ |
| w_rate_diff | {'estimate': 0.15972222222222224, 'low': 0.06927083333333334, 'high': 0.28472222222222227} [live] | — | — |
| w_rate_diff_ci_low | 0.06927 [live] | > 0 | ○ |


## Verdict
PASS

Note (ADR-046, written before the corpus was seen and kept regardless of the outcome): the planted version c13_minimal_fix did not pass any of the three ADR-037 operation checks (spec_literal 0/10 x 3, k* = 2). It is a plant that did not pass the operation check, and its mechanism is not specialization (it is local workarounds and instruction violations, i.e. changes to shared code); the c13 - c01 difference must not be read as detecting specialization. Had w_rate_diff_ci_low been <= 0, the c13-dependent criteria would have failed as a design-side FAIL (the plant misfired), not as a NEGATIVE for the method.

Designer's verdict (W5-A3, after seeing the corpus; criteria, thresholds and the plant's status unchanged): PASS. All four criteria were met, and since a difference appeared, ADR-046's "when a difference appears" reading applies. The W-rate difference is 0.160 [0.069, 0.285] [live] (W: c01 11/144, c13 34/144), above the W3-A2 MDE of 0.131-0.143. The visible pass-rate difference is -0.049 [-0.132, 0.007] [live]: read this as "no detectable difference in visible pass rate", not as a rank reversal, and it comes almost entirely from max_tokens truncations (F), a path separate from W. rob_c01_over_c13 = 1.0 [simulated] carries little information: c13 lost S into both W and F, so c01 wins everywhere in Theta. The null control holds (null_confident_fraction_c12 0.00067), while the point estimate rob_c12_vs_c01 sticks at 1.0. Specialization was detected in 1/144 c13 corpus runs and 0/144 c01 runs. Not verified: detection of specialization, how often this degradation occurs with realistic prompts, Rob's ability to reveal assumption dependence, the W estimator e-hat (stage 2), partial fixes missed by the hidden tests, and the mechanism of c13's extra W.

## Findings and limitations
- Hidden-test strength (hand-over from W1-I4): the hidden tests are bank minus K plus h cases, so a partial fix of only some of K can count as S, and each version's W rate is closer to a lower bound. Mutation score of the 24 tasks: mean 0.846, min 0.723 (generation-time metadata, not run observations, so labelled simulated). c13's local workarounds fix only the path the failing tests exercise, so partial fixes are probably more frequent in c13, which would bias the W-rate difference 0.160 downwards (conservative for H1'); this is not verified.
- Rob depends only on the direction of the point-estimate difference and sticks near 0 or 1 even when the true difference is zero: in control c12, rob_c12_vs_c01 = 1.000 (c12's pi_S 0.938 happened to exceed c01's 0.910), while the fraction of thetas where the decision is confident, null_confident_fraction_c12, is 0.001 (nominal 95% simultaneous bands; actual coverage at 24 clusters is about 0.85). Reading the point Rob alone would misread c12 as surely better; this is why Rob and the bands are reported side by side. visible_vs_nv_rank_reversal combines live (visible-rate interval) and simulated (Rob over Theta) values and carries the more conservative label, simulated (ADR-004).
- Rob's attainable ceiling depends on where c13's extra W comes from (0.887 for pure F-to-W, approaching 1 as more comes from S); within that range, missing 0.9 reflects assumption dependence ("trading W for F reverses the decision in about 11% of the assumptions"), not a measurement failure. In this corpus c01's F is only 0.014 (baseline pi_S 0.910, above the 0.5-0.8 band; ADR-043), so c13's extra W came mostly from S and rob_c01_over_c13 = 1.0 follows almost automatically (Theta-wide variant 0.986); the F-W trade where Rob would matter did not occur, so Rob's values below 1 were not exercised.
- Recorded only: NaNs of the break-even pi_S* are split into always-gain, always-loss and undefined in breakeven_pi_s, and quantiles use only defined thetas. Test-file modifications (test_files_modified, 0 for all versions) are recorded only and evaluation uses the original test files; vis_fail_hidden_pass (runs failing visible but passing hidden tests) is 0. Cost per run: c13 0.0476 USD, c01 0.0497 USD [live], negligible compared with the cost of cleaning up W.
- Where the c13 difference comes from [live, counted by the designer from outcomes and traces]: c13's 34 W runs fall in 11 of 24 tasks, 22 of them in four tasks; all 48 W runs across versions fall in 12 tasks (Kish effective number about 6.2), so cluster-bootstrap coverage may be below nominal. CT-textmeter-003 is W in every repeat of c01, c12 and c13, a local workaround that even the baseline makes; W was not split by mechanism, so the cause of c13's extra W is not established. 10 of the 11 F runs are responses truncated at max_tokens (2,048) and 1 hit max_steps; no run finished with failing visible tests. Stage-2 e-hat is not supported by this corpus: it would need about 120 or more W runs spread over about 30 or more tasks and all six repositories (open-questions.md).

---
Generated by `uv run agenteval exp ec_r1` / run at 2026-11-08T00:57:09+00:00 / cost 0.0 USD
