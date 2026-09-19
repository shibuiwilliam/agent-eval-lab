# Live Verification Report, Round 2 (carrying out R1–R5)

*日本語版: [LIVE2.md](LIVE2.md)*

Date: 2026-09-13 (second run the same day) / agent `claude-haiku-4-5-20251001` / judge `claude-sonnet-5`
A record of carrying out R1–R5 from `IMPROVEMENT.md`. Round 1 is [LIVE.en.md](LIVE.en.md).

## 1. Conclusion first

Round 1 of live verification found defects on the **agent** side.
Round 2 found three defects on the **evaluator and corpus** side. None of them are detectable with sim alone.

| # | What was found | Impact |
|---|---|---|
| L6 | **The planted defect in v02_dateformat was not reproducing on the real model** | v02's "flipped tests" were an artefact of sim. E3-2 / E3-4 / E8-5 depended on them |
| L7 | The composite model-fingerprint statistic was diluting its signal with non-discriminating terms | Under live it failed to detect the model swap at p = 0.118 (on the artificial distribution it had been p = 0.002) |
| — | The surrogate judge **does not agree** with the live judge (within ±1: 0.683; exact: 0.35) | The absolute score level of judge-based experiments cannot be discussed using sim |

In addition, **one diagnosis from round 1 was refuted by live and withdrawn** (R4 / ADR-014).

## 2. R1: move the judge to live → experiment [E0-2](E0-2.md)

The same 30 steps as E4-5 plus 3 mutation types (120 items in total) were judged both by live Sonnet 5 and by the deterministic surrogate judge.

| Metric | Criterion | Result |
|---|---|---|
| Surrogate ↔ live agreement (within ±1) | ≥ 0.7 | **0.683** ✗ |
| Degradation rate on mutated steps (live judge) | ≥ 0.8 | 0.989 ○ |
| **Re-judge agreement (live judge)** | ≥ 0.8 | **1.000** (exact 0.967) ○ |
| Mean score of the original steps (live) | ≥ 3.5 | 3.70 ○ |

Verdict: **NEGATIVE**. The second half of the hypothesis (the live judge is stable) held strongly; the first half (the surrogate agrees) did not.

The disagreement is one-directional. **The surrogate judge is consistently too lenient.**

| | Original step | Wrong tool | Wrong argument | Destructive operation |
|---|---|---|---|---|
| live Sonnet 5 | 3.70 | **1.73** | 1.37 | 1.00 |
| Surrogate judge | 4.87 | **4.00** | 1.97 | 1.00 |

`offline_step_judge` only deducts when an action is "not in the expected tool set and not a read", so it barely punishes substitution with a read-type tool. Live Sonnet 5 deducted heavily, treating it as "an action that makes no sense in this situation".

**Effect on E4-5**: all 4 of E4-5's criteria are met under live too. E4-5's conclusion (mutated steps score lower) is supported under live as well. What cannot be trusted is the **absolute score level**, not the direction of mutation detection.

**This is an item sim could not measure in principle.** E4-5's `rejudge_agreement = 1.0` was 1.0 only because the surrogate judge is deterministic — it was not actually verifying judging stability. Measured live, it is 1.000 within ±1 and 0.967 exact: genuinely stable.

## 3. R2: take the model fingerprint live → [E8-6](E8-6.md)

30 probes plus 10 mini-tasks were sampled twice with Haiku 4.5 and once with Sonnet 5 (120 calls).

| Statistic | Control (same model twice) | Swap (Haiku → Sonnet) |
|---|---|---|
| Composite (length + first-character agreement + tool selection) = pre-revision | p = 1.000 | **p = 0.118** (not detected) |
| Response length only (ADR-013) = post-revision | p = 0.716 | **p = 0.046** (detected) |

On the artificial distribution even the composite statistic detected the swap at p = 0.002. **That result had been propped up by the artificial distribution.**

The cause is that two terms of the composite statistic take the same value under the null and the alternative:
- first-20-character agreement rate: 0.2 even for the same model twice, 0.1 across models
- mini-task tool selection: **8 of 10 collapsed to `__no_tool__`** (the real model answers in prose instead of calling a tool — the same phenomenon as L1 in round 1)

ADR-013 adds a response-length-only permutation test and uses it for the live verdict.
**No criterion was loosened**: the two criteria against the artificial distribution remain, and two live criteria were added (making the verdict stricter).
The swap's p = 0.046 is right at the edge of 0.05 with 30 probes; stability requires more.

## 4. R3: E0-1's criterion was not rewritten

`version_ordering_preserved` was believed to be unmet "because the ordering of near-tied quantities was made a criterion". Increasing repeats from 3 to 6 (n = 60 per version) and re-measuring showed **zero version pairs with non-overlapping confidence intervals** (95% half-width ±0.063). v01 / v03 / v07 have the same pass rate under both live and sim: **there was no version pair against which the ordering could be verified at all.**

Rather than rewrite the criterion, a version against which ordering *can* be verified (v02_dateformat) was added to the comparison. The diagnostic metrics `n_separable_version_pairs` and `ordering_ok_on_separable_pairs` were added.

## 5. R4: withdrawn because live refuted its premise (ADR-014)

The FAIL of E3-3 / E3-7 had been diagnosed as "the redundancy penalty's similarity uses only the tool-name sequence, so tasks of the same kind mark each other as duplicates", and a revision including normalised arguments was planned. Re-measuring on live trajectories, the premise did not hold.

| | Mean similarity between distinct tasks | Pairs above 0.8 | Distinct tool-name sequences |
|---|---|---|---|
| live (10 tasks) | 0.257 | 1/45 = **2%** | 9 |
| sim (the same 10 tasks) | 0.431 | 3/45 = 7% | — |
| sim (all 46 tasks) | 0.455 | 115/1035 = **11%** | **13** (the largest cluster has 10 tasks on one sequence) |

Real-agent trajectories vary enough per task, and the redundancy penalty drops only genuinely similar tests, as intended. What was collapsed was **the diversity of the simulated agent's trajectories**. The similarity definition was left unchanged and the diagnoses in E3-3 / E3-7 were corrected.

## 6. L6: the planted defect was never planted

Adding v02_dateformat to E0-1 and running live gave a pass rate of 0.90 (v01 is 0.933), conflicting with sim's 0.617.

| | `start` argument of `calendar_create` | Prose to the user |
|---|---|---|
| live (12 cases) | `2026-09-21T15:00` (**still ISO**) | "next Monday (2026/09/21)…" |
| sim (100 cases) | `2026/09/21T15:00` | slashes |

**When the system prompt and a tool definition conflict, the real model prefers the tool definition.** The instruction "use YYYY/MM/DD" was applied only to the prose addressed to the user.

v02's prompt was rebuilt to state that "this convention takes precedence over the tool descriptions; pass `calendar_create`'s start / end in slash format too".
Verifying with 20 live runs, **8 of 8** `calendar_create` arguments came out in slash format and the pass rate fell from 0.90 to **0.60**. The planted defect now bites on the real model as well.

This is not a change of criteria — it is **fixing a planted defect that had never been planted**.

## 7. Cost

| Stage | Calls | USD |
|---|---|---|
| Round 1 (LIVE.md) | 354 | 0.81 |
| R1 E0-2 judge | 150 | 0.64 |
| R2 E8-6 fingerprint | 120 | 0.11 |
| R5 E0-1 extra repeats (90 runs) | 270 | 0.65 |
| L6 v02 live verification and re-sampling (120 runs) | 360 | 0.92 |
| Total | **1,308** | **3.09** |

That is 2.6% of the 120 USD budget. Live runs total 240 (10 tasks × 4 versions × 6 repeats).

## 8. Change in verdicts

| | After round 1 | After round 2 |
|---|---|---|
| PASS | 14 | 14 |
| NEGATIVE | 3 | **4** |
| FAIL | 9 | 9 |
| Experiments | 26 | **27** |

- **E0-2 is newly NEGATIVE** (the surrogate judge does not agree with the live judge)
- **E8-6 retained PASS**, but its content changed: the verdict now comes from the live fingerprint rather than the artificial distribution, and the failure of the pre-revision composite statistic under live is reported alongside it
- E3-2 / E3-4 / E8-5 keep their verdicts, but **the flips they rely on now happen on the real model too** (because v02's planted defect was rebuilt). Before round 2 they relied on flips that existed only in sim

## 9. Still outstanding

| # | Item | Estimate |
|---|---|---|
| R6 | Re-take E4-2 (the ablation surrogate metric) and E4-6 (deviation justification) with a live judge | 70 calls / 0.3 USD |
| R7 | Increase the fingerprint probes (30 leaves the swap at a marginal p = 0.046). The mini-tasks collapse to `__no_tool__` 80% of the time and need redesigning | 0.2 USD |
| R8 | Sim's pass-rate level is 0.18 below live (`pass_rate_gap`). Decide whether to match the difficulty parameters to live or to state the level difference and use it as is | 0 USD (a decision) |
| R9 | Building a live corpus at the 46-task scale could change the conclusions of E3-3 / E3-7 (now that the monotony of sim trajectories is known to be the cause) | about 25 USD |
