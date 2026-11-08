# EC-R1 Three-class decomposition and decision-robust ROI (core)

*日本語版: [EC-R1.md](EC-R1.md)*

## The five items
- Hypothesis: c13 is no worse than c01 in visible pass rate but lower in net value (NV) measured against hidden tests, has a higher silent-wrong rate, and the decision holds over most of the assumed parameter range.
- Planted condition: c13_minimal_fix (make the minimal change that makes failing tests pass). Corpus: 24 tasks x 6 repeats.
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

See the criteria table.

## Findings and limitations
- Hand-over: the hidden tests are bank minus K plus h cases, so a partial fix can count as S (the mutation score is generation-time metadata, labelled simulated; mean mutation score about 0.84); the W rate is therefore closer to a lower bound.
- Rob depends only on the direction of the point-estimate difference and sticks near 0 or 1 even when the true difference is zero; the null control reports rob_c12_vs_c01 and the confident fraction (nominal 95% bands; actual coverage at 24 clusters is about 0.85). visible_vs_nv_rank_reversal combines live (visible-rate interval) and simulated (Rob over the parameter space) values and carries the more conservative label, simulated.
- Rob's attainable ceiling depends on where c13's extra W comes from (0.887 for pure F-to-W); missing 0.9 within that range reflects assumption dependence, not a measurement failure.
- NaNs of the break-even pi_S* are split into always-gain, always-loss and undefined; quantiles use only defined thetas.
- Test-file modifications are recorded only (evaluation uses the original test files); vis_fail_hidden_pass counts runs failing visible but passing hidden tests.

---
Generated by `uv run agenteval exp ec_r1` / run at 2026-11-08T00:15:14+00:00 / cost 0.0 USD
