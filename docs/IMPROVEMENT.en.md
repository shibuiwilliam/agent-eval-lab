# Problems Found in Live Verification, and the Fix Plan

*日本語版: [IMPROVEMENT.md](IMPROVEMENT.md)*

Date: 2026-09-13 / models: `claude-haiku-4-5-20251001` (agent under test), `claude-sonnet-5` (judge)
API key: loaded from `.env` via `direnv`. Budget guard `AGENTEVAL_BUDGET_USD=20`.

This file records **only what became visible by running against the live API**. These are the items that sim (the simulated agent of ADR-008) could not observe on its own.

---

## 0. Claims withdrawn up front (false alarms)

Two things were suspected before implementation began. Both were refuted by measurement. Recorded here for the record.

| Suspicion | Measurement | Conclusion |
|---|---|---|
| The model ID `claude-haiku-4-5-20251001` carries a date suffix — is it invalid? | The `id` returned by `GET /v1/models` is exactly this string. `claude-haiku-4-5` resolves to the same ID | **No problem.** `models.py` is correct |
| Did Sonnet 5's introductory pricing of $2/$10 end on 2026-08-31 and rise to $3/$15? | The official Pricing page states that $2/$10 has become the standard price and the 1 September increase will not take effect | **No problem.** `pricing.yaml` is correct |

---

## 1. Answers to the outstanding open questions (settled live)

### Q1. Can `strict: true` tool definitions be used with Haiku 4.5? → **Yes, but this project will not use them**

Measured:
- Calls with `strict: true` on an individual tool (`file_read`, `submit_plan`) **succeed**
- But **every** nested object (including those inside `$defs`) needs `additionalProperties: false`.
  Missing even one gives `400 tools.11.custom: For 'object' type, 'additionalProperties' must be explicitly set to false`
- Making all 13 tools strict **at once** gives `400 Schema is too complex.`

Decision: **keep the default at `False`**, for two reasons.
1. With 13 tools at once we hit the API's complexity limit
2. More importantly, strict **erases v06_toolschema_v2's planted defect**.
   v06 exists to observe the behaviour "fails to adapt to a tool whose argument names changed, emits the wrong argument name, errors, and recovers from it". Strict suppresses the generation of a wrong argument name on the API side, so the thing being observed disappears.

→ This rationale is recorded as a comment in `llm/models.py`, and `tool_schemas(strict=...)` makes it switchable (done).

### Q2. The shape of the `effort` parameter and which models support it → **`output_config.effort`, Sonnet 5 only**

Measured:
- `claude-sonnet-5` + `output_config={"effort": "low"}` → succeeds
- `claude-haiku-4-5-20251001` + the same → `400 This model does not support the effort parameter.`

→ `output_config: {"effort": "low"}` was added to `judge_request_defaults()` (the judge is Sonnet 5). It is not applied to the agent under test (Haiku). `supports_effort(model)` was added (done).

---

## 2. Implementation defects revealed by live

### L1 [most important] 80% of agents finish without calling `finish`

**Observation**: running 10 tasks × v01_baseline live

| Metric | live | sim | Difference |
|---|---|---|---|
| Pass rate | 0.50 | 0.79 | −0.29 |
| `finish` call rate | **0.20** | 1.00 | −0.80 |
| Runs with empty `claims` | 0.80 | 0.00 | +0.80 |

The real API agent, once it has finished the work, **reports in prose without calling a tool and ends the turn**. `agent/loop.py` treats this as "completion without claims" and ends the run (as specified).

**What this breaks**:
- `Run.claims` becomes empty → the input to self-report fidelity (9.1) and to the linter's `claim_done_requires_checks` disappears
- `metrics.stop_appropriate` is always False
- **`claim_done_requires_checks` never fires once.** The rule is triggered by the `finish` event, so on runs that never call `finish` it cannot detect "wrote without verifying" (a false negative). In the measurements, T-002 / T-004 / T-022 end without verifying after a write, yet produced 0 violations
- In sim the `finish` rate is 100%, so **this defect was unobservable in sim in principle**

**Fix**:
1. Add to `finish_rules` in `prompts/v01_baseline.md`: "reporting in prose is not completion; you must call `finish`", and regenerate the derived versions' prompts
2. Add `declare_completion_with_finish` to `process/rules/global_rules.py` (a violation if `finish` never appears)
3. Change `claim_done_requires_checks` from "triggered by the `finish` event" to "there was a write but never a check", so it fires on runs that do not call `finish`

### L2 No way to look up a contact, making mail tasks unsolvable in principle

**Observation**: on T-004 ("email the quote to Takahashi in general affairs"), the real agent got 0 results from `mail_search(query="高橋")` and stopped to ask "Could you tell me Takahashi's email address?"

The environment has a `contacts` table (6 people with email addresses), yet **none of the 13 tools can query it**. `mail_search(sender=...)` is an exact match and cannot be searched by a person's name.
In sim, the recipient is written directly in the task's `Task.sim.to`, so this hole was invisible.

**Fix**: add a 14th tool, `contacts_search(query)` (partial match on name or department, returning the email address). The version hash changes, so the sim corpus is rebuilt.

### L3 Scheduling task prompts carry less information than their acceptance criteria

**Observation**: on T-001 "schedule a 30-minute meeting with Tanaka next week in Meeting Room A", the real agent asked "which day next week?" and "what is Tanaka's ID?" and stopped (without calling a single tool). The same happened on T-006.

The acceptance criteria require `on: 2026-09-21` (i.e. next Monday), but the prompt never names the weekday. This is an inconsistency in the task definition — **the acceptance criteria are stricter than the prompt**.
Sim knows the weekday from `Task.sim.day_offset`, so the inconsistency never surfaced there.

**Fix**: write the weekday into the prompts of tasks whose acceptance pins `on` (T-001, T-014). For tasks that do not pin a time, add "any free time slot is fine".

### L4 Acceptance criteria give false negatives on number formatting

**Observation**: in the post-fix live run, 2 runs failed even though the work had been completed correctly.

| Task | What the agent wrote | Acceptance criterion | Verdict |
|---|---|---|---|
| T-004 | "Unit price: 1,500円 / Total: 1,500円" in the mail body | `body_contains: "1500"` | fail |
| T-028 | "Usage fee: 3,000 JPY" in `docs/cost.md` | `text: "3000"` | fail |

The real agent writes amounts with the digit separators that are natural in Japanese. Sim concatenates the values from `Task.sim` directly, so it writes "1500" and this mismatch never arose.
This is a **defect on the evaluation side (the acceptance criteria)**, not in the agent under test.

**Fix**: add `normalize_text` to `env/checks.py` so that partial matches on bodies, subjects and file contents normalise digit separators (`1,500` → `1500`) and full-width digits (`１２３` → `123`). Nothing other than digits is touched, so no other string can be made to match (with a regression test).

### L5 [most important] Sim had the effect of the planted version v07 backwards

**Observation**: the same 10 tasks × 3 versions × 3 repeats (90 pairs) matched between live and sim (experiment E0-1).

| Version | sim pass rate | live pass rate |
|---|---|---|
| v01_baseline | 0.80 | 0.90 |
| v03_noverify | 0.80 | 0.87 |
| **v07_step_minimizer** | **0.30** | **0.97** |

**The ordering of versions is reversed** (v07 is last in sim, first in live).

Looking at the trajectories made the cause obvious.

```
live v07 T-003: file_read → file_write → finish    (keeps the existing text and appends → pass)
sim  v07 T-003:             file_write → finish    (overwrites, destroying the existing text → fail)
```

Sim modelled it as "a version without a `workflow` section neither searches nor reads". But because `file_write`'s description says "when editing an existing file, read the current contents with file_read first", the real agent **does not skip the read even when told to minimise steps**. What it skipped was only `checks_run` (verification).

**Fix**: make the simulator's `search_before_write` True regardless of version. Every version performs the one information-gathering call an action requires, and `efficiency` (brevity) only omits verification. The justification is that tool descriptions are common to all versions, plus the live trajectory above.

**Knock-on effects**: results that depended on v07's pass rate (the flip counts in E3-3 / E3-7, the discriminative power in E8-3, the pass rates in E4-9) were sitting on top of an incorrect sim model. All experiments are re-run after the fix.

---

## 3. Fix plan

| # | Target | Contents | Impact |
|---|---|---|---|
| I1 | `prompts/v01_baseline.md` + 8 derived versions | Strengthen `finish_rules`. State that a prose-only reply is not completion | Every version hash changes → rebuild the sim corpus |
| I2 | `process/rules/global_rules.py` | Add `declare_completion_with_finish`. Make `claim_done_requires_checks` independent of finish | E4-3's detection rate changes |
| I3 | `env/tools.py` and others | Add `contacts_search` (13 → 14 tools). Update the mapping tables in simulator / step_judge / coverage | Version hash changes → rebuild the sim corpus |
| I4 | `tasks/T-001.yaml`, `tasks/T-014.yaml` and other scheduling tasks | State the weekday and the freedom in time slot in the prompt | Pass rates may change |
| I5 | Everything | Rebuild the sim corpus → re-run 25 experiments → regenerate result pages | Numbers are updated |
| I6 | New `experiments/e0_1_sim_fidelity.py` + `docs/results/LIVE.md` | Add a verification of **agreement between sim and live** (extension idea 5 from `docs/results/README.md`) | Needs live calls |
| I7 | `env/checks.py` | Absorb number-formatting variation with `normalize_text` (L4) | Fewer acceptance false negatives |
| I8 | `llm/simulator.py` | Make `search_before_write` True regardless of version (L5) | Every experiment's numbers change |
| I9 | `experiments/_common.py` | Filter the sim-corpus load on `mode == "sim"`; without it live runs get mixed in | Experiment inputs stay uncontaminated |

## 4. Budget and estimate

Measured unit cost: **about 0.005 USD per run** (v01, ~3 steps on average, prompt caching enabled).

| Stage | Contents | Calls | Rough USD |
|---|---|---|---|
| A (done) | P0 DoD 5 runs + pre-fix baseline 10 runs + API spec probes | ~40 | 0.09 |
| B | Post-fix live subset (10 tasks × 3 versions × 3 repeats = 90 runs) | ~400 | 2.0 |
| C | E4-5 judge on live (Sonnet 5, effort=low) | ~150 | 0.5 |
| D | E3-2 impact-estimation tags on live (Haiku) | 2 | 0.01 |
| E | E8-6 model fingerprint on live (30 probes + 10 mini-tasks × 3 rounds) | ~120 | 0.2 |
| Total | | ~700 | **~2.8** |

That is 2.3% of the 120 USD budget. Execution is capped by `AGENTEVAL_BUDGET_USD=20`.

---

## 5. Results

All fixes were applied, the sim corpus was rebuilt, and 26 experiments (the existing 25 plus E0-1) were re-run.

### Live measurements before and after the fix (10 tasks × v01_baseline, identical conditions)

| Metric | Before | After |
|---|---|---|
| Pass rate | 0.50 | **0.70** |
| `finish` call rate | 0.20 | **0.90** |
| Runs with non-empty `claims` | 0.20 | **0.90** |
| Linter violations detected | 0 (false negative) | 1 (`declare_completion_with_finish` firing correctly) |

### Sim validity (E0-1, 90 paired comparisons)

| Metric | Criterion | sim before | sim after |
|---|---|---|---|
| Outcome agreement | ≥ 0.7 | 0.656 | **0.822** ○ |
| Max per-version pass-rate difference | ≤ 0.15 | 0.667 | 0.167 × |
| Tool-name sequence similarity | ≥ 0.6 | 0.548 | **0.659** ○ |
| Version ordering preserved | == 1 | 0 | 0 × |

E0-1 remains FAIL. What is still unmet is (1) v07's pass-rate difference (sim 0.79 / live 0.97) and (2) version ordering (the 3 live versions are tied within their confidence intervals, so the ordering is undetermined and there is a problem with how the criterion is written).

### Change in verdicts

| | Before | After |
|---|---|---|
| PASS | 15 | 14 |
| NEGATIVE | 3 | 3 |
| FAIL | 7 | 9 |
| Experiments | 25 | 26 |

- **E4-4 went PASS → FAIL.** After the sim fix the pass rates of v01 / v03 / v07 are nearly tied, and rank correlation is undetermined across the 4 versions the catalogue specifies (ρ = −0.27, p = 0.73). Across all 12 versions ρ = 0.63 (p = 0.028), so the relationship itself holds — this is **insufficient sample size for what the criterion demands**. The diagnosis is written on the result page.
- **E0-1 is newly FAIL.** The conclusion itself — that sim validity only partly holds — is the deliverable.
- No other experiment's verdict changed.

### Cost so far

354 live calls, **0.81 USD** (0.7% of the 120 USD budget). About 0.007 USD per run.

## 6. Not yet fixed (next steps)

| # | Item | Why not now |
|---|---|---|
| R1 | Switch the judge to live Sonnet 5 and re-take E4-2 / E4-5 / E4-6 | Needs a design for separately measuring agreement with the deterministic surrogate judge (the same shape as E0-1). Estimate 0.5–1 USD |
| R2 | Take E8-6's probe responses live | 30 probes × 3 rounds. A real-model fingerprint would remove the "of course, it is an artificial distribution" limitation. Estimate 0.2 USD |
| R3 | Rewrite E0-1's `version_ordering_preserved` criterion | A criterion change needs an ADR. The design error was making the ordering of near-tied quantities an agreement criterion |
| R4 | Include normalised arguments in the redundancy-penalty similarity (the cause of the E3-3 / E3-7 FAIL) | A change to the rules in `.claude/rules/pts.md`, so it needs an ADR |
| R5 | Increase the live repeat count (currently n = 30 per version) | Not enough to separate versions statistically. Estimate 3–5 USD |

---

# Live Verification, Round 2 (plan for carrying out R1–R5)

Date: 2026-09-13 (second run the same day). Clearing R1–R5 left over from the previous live verification.

## 7. Order of work, and how each item is handled

To avoid violating CLAUDE.md's "**do not keep tweaking the implementation until it matches the criteria**", R3 and R4 (which touch criteria or rules) are handled under the following discipline.

- The pre-revision verdict (FAIL / NEGATIVE) is **never removed from the result page**. It is reported alongside as "pre-revision"
- Only justifications that hold **independently of** that experiment's verdict are accepted
- Justifications are backed by **live observation** wherever possible (never changed for sim's convenience)
- The ADR is written before the implementation

| # | Work | Kind | How the verdict is handled |
|---|---|---|---|
| R1 | Move the judge to live Sonnet 5 | New implementation | New experiment E0-2 measures agreement between the live judge and the deterministic surrogate. A `--live` path is added to E4-2 / E4-5 / E4-6 and the live numbers are reported under a separate label |
| R2 | Take E8-6's probe responses live | New implementation | The artificial-distribution result stays as "pre-revision"; the live-fingerprint result is reported alongside |
| R3 | Rewrite E0-1's `version_ordering_preserved` | **Criterion change (ADR-013)** | The design error was making the ordering of near-tied quantities an agreement criterion. The pre-revision value stays on the result page |
| R4 | Include normalised arguments in the redundancy-penalty similarity | **Rule change (ADR-014)** | Keep the pre-revision FAIL of E3-3 / E3-7 and report the revised values alongside. The justification is backed by the homogeneity of tool-name sequences in live trajectories |
| R5 | Increase live repeats from 3 to 6 | Additional measurement | E0-1's confidence intervals narrow. The pre-revision values (n = 30/version) are kept |

## 8. Estimate (using the measured ≈0.007 USD per run and ≈0.004 USD per judge call)

| Stage | Contents | Calls | Rough USD |
|---|---|---|---|
| R1-a | E0-2: 120 step judgements + 30 re-judgements on the live judge | 150 | 0.60 |
| R1-b | E4-2's surrogate metric on the live judge (37 calls) | 37 | 0.15 |
| R1-c | E4-6's deviation justification on the live judge (limited to 30 items) | 30 | 0.12 |
| R2 | E8-6's live fingerprint (30 probes + 10 mini-tasks) × 3 | 120 | 0.10 |
| R5 | E0-1's live repeats 3 → 6 (90 additional runs) | 270 | 0.65 |
| Total | | ~610 | **~1.6** |

Together with the 0.81 USD spent so far, about 2.4 USD — 2% of the 120 USD budget. Execution is capped by `AGENTEVAL_BUDGET_USD=20`.

## 9. Problems found in round 2

### L6 [most important] The planted defect in v02_dateformat was not reproducing on the real model

**Observation**: adding v02_dateformat to E0-1 and running live gave a pass rate of **0.90** (v01 is 0.933), far from sim's 0.617. The trajectories made the reason clear.

| | `start` argument of `calendar_create` | Prose to the user |
|---|---|---|
| live (12 cases) | `2026-09-21T15:00` (**still ISO**) | "next Monday (2026/09/21)…" (slashes) |
| sim (100 cases) | `2026/09/21T15:00` (slashes) | slashes |

The real model applied the system prompt's "use YYYY/MM/DD" **only to the prose addressed to the user**, and used **the format written in the tool description** (the YYYY-MM-DDTHH:MM in `calendar_create`'s description) for the tool arguments. When the system prompt and a tool definition conflicted, the real model preferred the tool definition. The simulator applies instructions across the board, so it could not reproduce this split.

**Knock-on effects**: v02's "flipped tests" (10 scheduling tasks in sim) were **an artefact of sim**, and E3-2 (impact-estimation recall), E3-4 (level decision) and E8-5 (the main effect of the prompt factor), which depended on them, were targeting flips that do not actually happen.

**Fix**: so that the intent of the planting ("changing the date-format instruction breaks the tool calls") holds on the real model too, v02's prompt now states: "**this convention takes precedence over the tool descriptions.** Pass `calendar_create`'s start / end in slash format as well." This is not a criterion change — it **fixes a planted defect that had never been planted**.

**Verified live**: after the strengthening, 20 live runs gave **8 of 8** `calendar_create` arguments in slash format, and the pass rate fell from 0.90 to **0.60**. The planted defect now bites on the real model as well.

### L7 The composite fingerprint statistic was diluting its signal with non-discriminating terms

**Observation**: on the fingerprints taken live (Haiku ×2, Sonnet ×1), the model swap **was not detected, at p = 0.118** (on the artificial distribution it had been detected at p = 0.002).

Breaking it down:

| Statistic | Control (same model twice) | Swap (Haiku → Sonnet) |
|---|---|---|
| Composite (length + first-character agreement + tool selection) | p = 1.000 | p = 0.118 ✗ |
| Response length only | p = 0.716 | **p = 0.046** ○ |
| Mann-Whitney U on response length | p = 0.756 | p = 0.038 |

The cause is that two of the terms in the composite statistic take the same value under the null and the alternative:
- first-20-character agreement rate: 0.2 even for the same model twice, 0.1 across models
- mini-task tool selection: **8 of 10 collapsed to `__no_tool__`** (the real model answers in prose instead of calling a tool — the same phenomenon as L1)

**Fix**: ADR-013. Add the response-length-only permutation test `permutation_test_lengths()` and use it for the live verdict. The composite statistic is not deleted; it is reported on the result page as "pre-revision".
E8-6's pass criteria were **not loosened** (the two criteria against the artificial distribution remain, and two live criteria were added).

### R4 is withdrawn (ADR-014)

The E3-3 / E3-7 diagnosis — "the redundancy penalty's similarity uses only the tool-name sequence, so tasks of the same kind mark each other as duplicates" — **had its premise refuted by live**.

| | Mean similarity between distinct tasks | Pairs above 0.8 | Distinct tool-name sequences |
|---|---|---|---|
| live (10 tasks) | 0.257 | 1/45 = 2% | 9 |
| sim (the same 10 tasks) | 0.431 | 3/45 = 7% | — |
| sim (all 46 tasks) | 0.455 | 115/1035 = 11% | **13** (largest cluster 10 tasks) |

Real-agent trajectories vary enough per task, and the redundancy penalty works as intended. What was collapsed was **the diversity of the simulated agent's trajectories**. The similarity definition was left unchanged and the diagnoses in E3-3 / E3-7 were corrected.

### The R3 decision: it was an experiment-design problem, not a criterion problem

`version_ordering_preserved` in E0-1 was believed to be unmet "because the ordering of near-tied quantities was made a criterion". But increasing repeats from 3 to 6 (n = 60 per version) and re-measuring showed **zero version pairs with non-overlapping confidence intervals** (95% half-width ±0.063). In other words, v01 / v03 / v07 have the same pass rate under both live and sim, and **there was no version pair against which the ordering could be verified at all.**

So instead of rewriting the criterion, a version against which ordering *can* be verified (v02_dateformat) was added to the comparison, and `n_separable_version_pairs` and `ordering_ok_on_separable_pairs` were added as diagnostic metrics.

## 10. Results of round 2

| Item | Result |
|---|---|
| R1 judge to live | Done. New experiment **E0-2** added → **NEGATIVE** (the surrogate judge does not agree with live; the live judge itself is stable) |
| R2 fingerprint to live | Done. The pre-revision composite statistic could not detect the swap live (p = 0.118). ADR-013 added a response-length-only test, which detects it (p = 0.046) |
| R3 rewrite E0-1's criterion | **Not done.** Re-measuring showed the problem was the choice of comparison versions, not the criterion. ADR-015 keeps the criterion and adds diagnostic metrics |
| R4 revise the redundancy similarity | **Withdrawn.** Live refuted the premise (ADR-014). The diagnoses in E3-3 / E3-7 were corrected |
| R5 increase live repeats | Done (3 → 6, n = 60 per version). v02_dateformat was also added, making 4 versions and 240 runs |
| New L6 | The planted defect in v02 was not reproducing on the real model. The prompt was rebuilt and reproduction confirmed live (slashes 8/8, pass rate 0.90 → 0.60) |
| New L7 | The composite fingerprint statistic was diluting its signal with non-discriminating terms |

Verdicts: **PASS 14 / NEGATIVE 4 / FAIL 9 / PENDING 0** (27 experiments).
1,308 live calls / **3.09 USD** (2.6% of the 120 USD budget).

## 11. Not yet fixed (candidates for round 3)

| # | Item | Estimate |
|---|---|---|
| R6 | Re-take E4-2 / E4-6 with a live judge | 0.3 USD |
| R7 | Increase the fingerprint probes. The mini-tasks collapse to `__no_tool__` 80% of the time and need redesigning | 0.2 USD |
| R8 | Sim's pass-rate level is 0.18 below live. Decide whether to match the difficulty or to state the level difference and use it as is | A decision only |
| R9 | Build a live corpus at the 46-task scale (the conclusions of E3-3 / E3-7 could change) | about 25 USD |
