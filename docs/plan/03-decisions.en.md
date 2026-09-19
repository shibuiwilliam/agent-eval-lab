# Architecture Decision Records (ADR)

*日本語版: [03-decisions.md](03-decisions.md)*

Format: `ADR-NNN Title` / Situation / Decision / Rationale / Consequences. New decisions are appended at the end; existing ADRs are never rewritten — they get a "superseded by" note instead.

## ADR-001 Use a hand-written loop instead of the SDK tool runner
- Situation: the official Python SDK ships a tool runner (beta, with automatic compaction) that automates the tool-execution loop
- Decision: do not use it. Drive `client.messages.create` in our own loop
- Rationale: branch re-execution (3.6) and ablation (4.3) require the ability to stop the conversation and the environment at a step boundary, take a snapshot, and resume from an arbitrary step. Automatic compaction would interfere with comparing context strategies (4.9)
- Consequences: we take responsibility for the ordering of `tool_result` blocks (they come first), for handling `stop_reason`, and for executing parallel tool calls correctly

## ADR-002 Use no sampling parameters; get determinism from record/replay
- Situation: the Sonnet 5 family returns 400 for non-default `temperature` and friends. Haiku 4.5 accepts them, but setting them still does not give full determinism
- Decision: set no sampling parameters on any model. Determinism for unit tests and recomputation comes from cassette replay. Live variance is handled with repeats (pass^k)
- Consequences: "two live runs of the same version differ" is by design. Provenance labels become mandatory so that replay and live numbers are never mixed

## ADR-003 Use a synthetic office environment rather than a coding environment
- Situation: a coding-agent environment (a git repository) is realistic, but each run is long and expensive, and injecting faults and drift is hard
- Decision: build a SQLite-based `office` environment (calendar, mail, files, external service)
- Rationale: snapshots are a file copy, state inspection is expressible in SQL, the injectors (fault / noise / drift) can live on the environment side, and each run is short
- Consequences: it does not reproduce real-world complexity. Result pages must say "verified in an illustrative environment". A coding environment stays as a future extension (`env/repo.py`) in design only

## ADR-004 Attach the provenance label live / replay / simulated to each individual number
- Situation: SPRT error rates and selection curves can be demonstrated by Monte Carlo, but that is easy to confuse with measurement
- Decision: attach a provenance to every number in the result JSON and display it on the result page and in the summary. Never build a statistic that mixes them
- Consequences: experiment scripts carry a label per number. `verify` rejects any number without a label

## ADR-005 Approximate plan-deviation granularity with the tool-name sequence
- Situation: section 4.7 of the report defines it as "edit distance taken at the granularity of semantic equivalence". Applying a semantic-equivalence judgement to every step would multiply judge calls
- Decision: compute the deviation rate δ as edit distance over the tool-name sequence, and use the judge only to rule on the justification of each deviation point
- Consequences: a deviation that differs only in arguments does not appear in δ. This is recorded as a limitation on the result page

## ADR-006 Substitute a generator for production traffic
- Situation: there is no real deployment, so the Chapter 8 work on production→test, distribution distance and dual-track cannot be verified against real data
- Decision: `ProdStream` generates sessions from a distribution that shifts day by day, and the runs produced by executing them under v01 are treated as "production"
- Consequences: provenance is `live (synthetic prod)`. This project makes no claim about effectiveness in real operation

## ADR-007 Fix pass criteria before implementation; change them only via an ADR
- Situation: when an experiment misses its criteria, there is a temptation either to loosen the criteria or to tweak the implementation
- Decision: criteria are written into `02-experiment-catalog.md` and `registry.yaml` before implementation, and any change comes with a new ADR. A miss is recorded as NEGATIVE
- Consequences: negative results become legitimate deliverables. `summary.md` gets a NEGATIVE column

## ADR-008 Where the live API is unavailable, build trajectories with a deterministic simulator
- Situation: in this session `ANTHROPIC_API_KEY` was not exported to the shell and `AGENTEVAL_BUDGET_USD` was unset, so the CLAUDE.md rules keep live execution from starting. Yet 11 of the 25 experiments assume live
- Decision: add `AGENTEVAL_LLM_MODE=sim` and build trajectories with the deterministic simulated agent in `llm/simulator.py`. Its behaviour is read from the **section markers in the version's system prompt**, and the task's intent comes from `Task.sim` (a field written independently of the acceptance criteria). Errors are drawn from a "luck" vector fixed by `(task_id, seed, repeat)`, and the same luck is reused across versions (making comparisons paired)
- Rationale: (1) verifying an evaluation method's implementation and its reaction only needs trajectories; (2) paired comparison across versions isolates the version effect with few repeats; (3) cost is zero, so repeats can be increased
- Consequences:
  - **every provenance label becomes `simulated` rather than `replay`**, and that shows up verbatim on result pages and in `summary.md`
  - experiments that detect a property deliberately embedded in the simulator's policy (e.g. that v04 repeats searches) **can be circular**. Each result page says so under "findings and limitations"
  - re-confirmation against live has not been done. `docs/results/README.md` keeps a list of what remains unverified
  - `tests/conftest.py` still forces `AGENTEVAL_LLM_MODE=replay` per the rules and blocks sockets. Tests that create runs build `LLMClient(mode="sim", simulator=...)` explicitly

## ADR-009 Define a "flip" as the disagreement rate over paired repeats
- Situation: the Chapter 3 experiments count "tests flipped by a change". Defining that by a pass-rate difference lets repeat-to-repeat noise mask the version effect when repeats are few. And when versions have different repeat counts, the same "luck" is not being compared, producing spurious flips
- Decision: a flip is when the fraction of **matched `(task_id, repeat)` pairs** whose outcome changed exceeds that task's flake band (the Wald 95% half-width from the baseline's repeats). Repeat counts are equalised across versions
- Rationale: the simulator is deterministic in `(task, seed, repeat)`, so a paired comparison cancels repeat noise and leaves only the version effect. The catalogue rule "a change inside the band does not count" applies unchanged
- Consequences: flip detection is sharper than a naive pass-rate difference. Under live, the same `(task, repeat)` still varies, so this definition holds precisely because it is sim. Re-confirming under live requires more repeats and a re-measured band

## ADR-010 Increase repeats in sim mode (separate from the live repeat count)
- Situation: `repeats` in `corpus.yaml` was chosen under the live budget constraint (5 for baseline, 3 for the rest). In sim the API cost is zero, so there is no reason to obey that constraint
- Decision: add `sim_repeats` to `corpus.yaml` and use 10 repeats for every version in sim mode. Live mode keeps the existing `repeats`
- Rationale: the flake band narrows and the version effect separates. Equal repeat counts across versions is also a prerequisite of ADR-009
- Consequences: the corpus is 46 tasks × 12 versions × 10 repeats = 5,520 runs, taking about 12 minutes to generate (parallelism 4). `data/` is git-ignored, so size is not an issue

## ADR-011 Fix the environment, the rules and the simulator based on live verification
- Situation: running against the real API with `ANTHROPIC_API_KEY` from `.env` surfaced 5 defects that sim alone could not observe (`IMPROVEMENT.md` L1–L5)
- Decision: fix the following. None of them change a pass criterion — they fix the observation side
  1. Add `contacts_search` as a 14th tool. The environment had a `contacts` table with no way to query it, making tasks that need a recipient unsolvable in principle
  2. Strengthen `finish_rules` in `v01_baseline` to state that a prose-only reply does not constitute completion, and regenerate the prompts of the 8 derived versions
  3. Change the linter's `claim_done_requires_checks` from being triggered by the `finish` event to being evaluated over the whole run, and add `declare_completion_with_finish`. Since 80% of real agents never call `finish`, the previous implementation produced false negatives
  4. Add `normalize_text` to `env/checks.py` to normalise digit separators and full-width digits. An acceptance criterion demanding `1500` while the agent writes `1,500円` is a defect on the evaluator side
  5. Make the simulator's `search_before_write` True regardless of version. Modelling it as "a version without a `workflow` section does not even read" produced results opposite to live (v07: sim 0.30 / live 0.97)
- Rationale: verifying an evaluation method only means something when both the object of evaluation (the agent) and the evaluator (criteria, linter) are sound. Live exposed defects in both
- Consequences: version hashes and tool schemas changed, so the sim corpus was rebuilt (46 tasks × 12 versions × 10 repeats). All experiments were re-run, and E4-4 changed from PASS to FAIL (the reason is on its result page). The live verification record is `docs/results/LIVE.md`

## ADR-012 Always separate live runs from the sim corpus
- Situation: `data/traces/` holds both live and sim `Run`s. `load_corpus()` returns everything, so there was a path by which an experiment could mix live runs into sim statistics
- Decision: filter at the experiment entry point (`corpus()` in `experiments/_common.py`) on `mode == "sim"`. Experiments that use live (E0-1) read `live__*` traces explicitly
- Rationale: ADR-004 (a provenance label per number) cannot be upheld on mixed input
- Consequences: mixed statistics can no longer be built. `agenteval verify` still rejects unlabelled numbers

## ADR-013 Drop the non-discriminating terms from the model-fingerprint statistic
- Situation: `statistic()` in `drift/fingerprint.py` summed "mean response-length difference + first-20-character mismatch rate + tool-selection distribution difference". Taking an actual live fingerprint (`IMPROVEMENT.md` R2) **failed to detect** the Haiku 4.5 → Sonnet 5 swap at p = 0.130 (on the artificial distribution it had been p = 0.002)
- Evidence (independent of the verdict):
  - the first-20-character agreement rate is **0.2 even when sampling the same model twice**, and 0.1 across models. It takes nearly the same value under the null and the alternative
  - tool selection on the 10 mini-tasks collapsed to `__no_tool__` on 8 of them (the real model answers in prose instead of calling a tool — the same phenomenon as `IMPROVEMENT.md` L1)
  - looking at response length alone separates cleanly: control p = 0.756 / swap p = 0.046 (Mann-Whitney U gives p = 0.038)
- Decision: add `permutation_test_lengths()`, which uses the response-length distribution only, and use it for the live verdict. The previous composite `statistic()` / `permutation_test()` is **not deleted** — it is reported alongside as "pre-revision"
- Rationale: a term that takes the same value under the null and the alternative only dilutes the signal of a permutation statistic. This argument stands without looking at whether the test passes
- Consequences: **this change was made after seeing the pre-revision result (p = 0.130)**. The evidence does not depend on the verdict, but readers need to know the order of events. It is stated on the result page and in `IMPROVEMENT.md`.
  E8-6's pass criteria were **not loosened**. The two existing criteria against the artificial distribution remain, and two live criteria (`live_control_p_value >= 0.05`, `live_swap_p_value < 0.05`) were **added** (making the verdict stricter)
- Remaining limitation: the swap's p = 0.046 is right at the edge of 0.05 with 30 probes. It will not be stable without more probes or a redesign of the tool-calling mini-tasks

## ADR-014 Do not change the redundancy-penalty similarity (withdrawing R4)
- Situation: the FAIL of E3-3 / E3-7 had been diagnosed as "the redundancy penalty's similarity uses only the tool-name sequence, so tasks of the same kind mark each other as duplicates", and a revision including normalised arguments was planned (`IMPROVEMENT.md` R4)
- Verification: measuring tool-name-sequence homogeneity on live trajectories **refuted the premise of the diagnosis**
  - live (10 tasks): mean similarity between distinct tasks 0.257; 1 of 45 pairs above 0.8 (2%); 9 distinct tool-name sequences across 10 tasks
  - sim (the same 10 tasks): mean 0.431; 3 pairs above 0.8 (7%). Across all 46 tasks there are only **13 distinct sequences**, with the largest cluster containing 10 tasks on an identical sequence
- Decision: **do not change** the similarity definition. R4 is withdrawn
- Rationale: real-agent trajectories vary enough per task, and the redundancy penalty drops only genuinely similar tests, as intended. What is collapsed is the simulated agent's trajectories
- Consequences: the cause of the E3-3 / E3-7 FAIL is corrected from "a limitation of the method (the similarity definition)" to "**insufficient trajectory diversity in sim**". The diagnoses on the result pages are rewritten. Building a live corpus of the same scale could change the conclusion (unverified)

## ADR-015 For E0-1's ordering criterion, add rather than replace
- Situation: E0-1's `version_ordering_preserved` (whether live and sim agree on the ordering of version pass rates) was unmet. The cause was believed to be "a design error in making the ordering of near-tied quantities a criterion", and a rewrite was planned (`IMPROVEMENT.md` R3)
- Verification: increasing live repeats from 3 to 6 (n = 60 per version) and re-measuring showed that the 3 versions being compared (v01 / v03 / v07) had **zero pairs with non-overlapping confidence intervals** (95% half-width ±0.063). There was no version pair against which the ordering could be verified at all
- Decision:
  1. Add v02_dateformat (a version whose sim pass rate is clearly lower) to the comparison to create verifiable pairs. That produced 2 separable pairs, and **on those pairs sim's ordering agreed with live**
  2. **Keep** `version_ordering_preserved` as a criterion. E0-1's verdict stays FAIL
  3. **Add** `ordering_ok_on_separable_pairs`, restricted to statistically separable pairs, and `n_separable_version_pairs >= 1`
- Rationale: replacing the criterion would make E0-1 PASS, but that is "tweaking until it matches the criteria" (CLAUDE.md). The fact that the criterion is ill-posed is written on the result page with numbers, and the verdict falls on the strict side
- Consequences: E0-1's criteria went from 4 to 6 (making the verdict stricter). The verdict remains FAIL
