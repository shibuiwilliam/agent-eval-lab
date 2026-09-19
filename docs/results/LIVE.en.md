# Live Verification Report (confirmation against the real API)

*日本語版: [LIVE.md](LIVE.md)*

Date: 2026-09-13 / agent under test `claude-haiku-4-5-20251001` / judge `claude-sonnet-5`
API key: loaded from `.env` via `direnv`. Budget guard `AGENTEVAL_BUDGET_USD=20`.
Cost incurred: **0.81 USD** (354 live calls). That is 0.7% of the 120 USD budget.

This report records what changes when the results obtained so far with sim alone (the simulated agent of ADR-008) are instead produced by **running against the real Claude API**.
The problems found and the fixes applied are detailed in [`IMPROVEMENT.en.md`](../IMPROVEMENT.en.md).

## 1. What was run

| Stage | Contents | Calls | Cost |
|---|---|---|---|
| Spec confirmation | `GET /v1/models`, `strict` tools, `effort`, forced `tool_choice`, prompt caching | 12 | 0.01 |
| Pre-fix baseline | 10 tasks × v01_baseline | 32 | 0.05 |
| Post-fix confirmation | the same 10 tasks × v01_baseline | 40 | 0.10 |
| E0-1 subset | 10 tasks × 3 versions × 3 repeats = 90 runs | 270 | 0.62 |
| Total | | **354** | **0.81** |

## 2. Two open questions now have answers

| Question | Measured result |
|---|---|
| Can `strict: true` tool definitions be used with Haiku 4.5? | **Yes**, but every nested object needs `additionalProperties: false`, and 13 tools at once gives `400 Schema is too complex.` Moreover, strict suppresses v06's planted defect (recovery from a wrong argument name) on the API side, erasing the thing being observed — so **this project does not use it** |
| The shape of `effort` and which models support it | `output_config: {"effort": "low"}`. **Sonnet 5 supports it; Haiku 4.5 returns 400** (`This model does not support the effort parameter.`). Applied to the judge (Sonnet 5) only |

## 3. Five defects that sim could not observe

| # | Defect | sim | live (before fix) | After fix |
|---|---|---|---|---|
| L1 | The agent ends with prose instead of calling `finish` | 0% | **80%** | 10% |
| L2 | No tool to look up a contact, so the agent stops without knowing the recipient | never happens (the intent carries the address) | occurs | resolved by adding `contacts_search` |
| L3 | The prompt carries less information than the acceptance criteria (no weekday) | never happens (the intent carries the date) | occurs (asks a question and stops) | resolved by stating the weekday in the prompt |
| L4 | Acceptance criteria give false negatives on number formatting (`1,500円` vs `1500`) | never happens | occurs (2 of 10 runs) | resolved by `normalize_text` |
| L5 | sim had the effect of the planted version v07 **backwards** | v07 = 0.30 | **v07 = 0.97** | sim fixed, now 0.79 |

### Live measurements before and after the fix (10 tasks × v01_baseline, identical conditions)

| Metric | Before | After |
|---|---|---|
| Pass rate | 0.50 | **0.70** |
| `finish` call rate | 0.20 | **0.90** |
| Runs with non-empty `claims` | 0.20 | **0.90** |
| Linter violations detected | 0 (false negative) | 1 (`declare_completion_with_finish`) |

L1 was breaking the evaluation method itself. `claim_done_requires_checks` was triggered by the `finish` event, so on runs that never call `finish` it could **never once detect** "wrote without verifying". The rule was changed to evaluate the whole run, and `declare_completion_with_finish` was added.

## 4. Sim validity (experiment [E0-1](E0-1.md))

Result of matching the same 90 (task, version, repeat) triples between live and sim.

| Metric | Criterion | sim before fix | sim after fix | Verdict |
|---|---|---|---|---|
| Outcome agreement | ≥ 0.7 | 0.656 | **0.822** | ○ |
| Max per-version pass-rate difference | ≤ 0.15 | 0.667 | 0.167 | × |
| Tool-name sequence similarity | ≥ 0.6 | 0.548 | **0.659** | ○ |
| Version ordering preserved | == 1 | 0 | 0 | × |

**Where sim agrees with live**: outcomes (0.82), step count (live 3.04 / sim 3.28), verification rate (live 0.29 / sim 0.33), duplicate-call rate (live 0.006 / sim 0.0).

**Where sim does not agree with live**:
- `stop_appropriate`: sim 1.0 / live 0.66. Sim always calls `finish`.
  **Metrics that depend on `finish` (claims, self-report fidelity, stop appropriateness) must not be discussed using sim numbers.**
- Version ordering: under live the 3 versions score 0.90 / 0.87 / 0.97, a spread that fits inside the per-version confidence interval (n = 30, ±0.11). The ordering itself is not statistically determined, so there is a problem with how this criterion is written.

## 5. Effect of live verification on existing results

| Experiment | Change | Reason |
|---|---|---|
| E4-4 Partial-order milestones | PASS → **FAIL** | After the sim fix the pass rates of v01 / v03 / v07 are nearly tied, and rank correlation is undetermined across 4 versions (ρ = −0.27, p = 0.73). Across all 12 versions ρ = 0.63 (p = 0.028), so the relationship itself does hold |
| E4-9 Goodhart pairs | PASS retained | v07's step ratio is 0.795 (criterion 0.8) with a verification rate of 0. Under live too, v07 omits only the checks — consistent with the claim |
| E8-5 Drift attribution | PASS retained | The fix to the factor-swap design is doing its job |
| E3-3 / E3-7 Selection | FAIL retained | The cause (the redundancy penalty's similarity) is independent of the sim fix |

## 6. Not yet confirmed under live

- E4-2 / E4-5 / E4-6 with the judge switched to live Sonnet 5 (currently a deterministic surrogate judge)
- Real probe responses for the E8-6 model fingerprint (currently an artificial distribution)
- The live cost of branch re-execution in E3-5 (NEGATIVE in sim)
- Live measurements with more repeats (currently n = 30 per version, not enough to separate versions)
