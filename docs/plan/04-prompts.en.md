# Prompts for Running Development and Verification

*日本語版: [04-prompts.md](04-prompts.md)*

Prompts to paste into Claude Code. The default is one prompt per session (when the context is used up, `/clear` and paste the "resume" prompt — CLAUDE.md and `.claude/rules/` are re-read, so nothing is lost).

## How to use them
1. In the shell, `export ANTHROPIC_API_KEY=...` and `export AGENTEVAL_BUDGET_USD=120`, then start `claude`
2. Run `/context` and confirm CLAUDE.md appears under Memory files
3. Paste the prompts below in order. When Claude stops, reply with either "approved" or "rejected: <reason>". Keep to these two words
4. Allowing `make check` and the `uv run` family in `/permissions` cuts down on confirmations. Do not allow `rm` or live execution (confirm those each time)
5. Expected session counts: P0 = 1–2, corpus = 1, P1 = 3–5, P2 = 5–7, P3 = 4, P4 = 6–9, P5 = 1. The live-heavy experiments (E3-5, E4-2, E4-5, E4-7, E8-4 through E8-7) get one session each

---

## 1. P0 kickoff

```text
# Goal
Complete P0 (foundations) of agent-eval-lab. Before starting, read CLAUDE.md, docs/plan/00-plan.md, docs/plan/01-architecture.md, the P0 section of docs/plan/progress.md, .claude/rules/llm-cost.md and .claude/rules/env-agent.md.

# How to proceed
1. First, present the P0 implementation plan in plan mode and stop. Include:
   - the list of files to create, with a one-line responsibility for each
   - the list of unit tests (state explicitly that none call the API, and describe how conftest.py blocks live)
   - draft contents for tasks T-001 through T-005 (category / kind / prompt / milestones / acceptance / l_min)
   - the outline of the v01_baseline system prompt (with section markers) and the policy for the descriptions of the 13 tools
   - the points you are unsure about and the assumptions you are making on the spot (candidates for open-questions.md)
   Do not implement until I write "approved".
2. After approval, implement in the order of the P0 items in progress.md. Pass `make check` after each item and commit (conventional commits).
3. Only two live calls: "recording T-001 through T-005 × v01" and "taking the model-fingerprint baseline for E8-6". Before running, present the call count and the rough USD together and wait for my "approved".
4. After recording, run replay twice and show in the output that the trace hashes match. If they do not, find the cause (missing normalisation, time dependence, randomness) and fix it.
5. Read unit prices from pricing.yaml. If you think verified_at is stale, ask me to check the official Pricing page.

# Include in the completion report
- the progress.md update (ticks and a "next" line)
- the output of `uv run agenteval cost`
- additions to open-questions.md (whether strict tool definitions work, the API shape of effort, anything else confirmed)
- anything that contradicted the report or CLAUDE.md during implementation (present it as a draft ADR; do not finalise an ADR yourself)

# Forbidden
- calling the API from pytest
- setting temperature / top_p / top_k
- writing unit prices or model IDs anywhere other than models.py and pricing.yaml
- running live before my approval
```

---

## 2. Building the corpus (first half of P1)

```text
# Goal
Following the corpus plan in docs/plan/00-plan.md and corpus.yaml, create the tasks, versions and corpus. Read .claude/rules/env-agent.md before starting.

# How to proceed
1. Present in plan mode and stop:
   - the task list (30 + do_nothing 3 + ambiguous 3 + niah 4 + trivial 2 + private 4). One line each: id / category / kind / one-line content / l_min / risk / visibility
   - planted.description and expected_effects for versions v02–v09, v01p, v02b_tone, v10_contaminated, v12_ignore_plan
   - the diff policy for each version's system prompt (only the diff from v01; name the section ids explicitly)
   - the tool-schema diff for v06_toolschema_v2
   Do not implement until "approved".
2. After approval, create the task YAMLs, version YAMLs and prompts, and add any needed check functions to env/checks.py. Confirm that every task's acceptance and milestones are evaluated at least once, either by a recorded P0 run or by a unit test over a synthetic trace.
3. Run `uv run agenteval corpus build --plan corpus.yaml --dry-run`, present the estimate table (calls, tokens, USD for version × task × repeat) and stop. Wait for "approved".
4. After approval, run with `--live` (parallelism 4). If it stops with BudgetExceeded, report the partial results and the estimate for the remainder, and stop.
5. Report on completion: per-version pass rates, v01's flake band (the spread of outcomes over 5 repeats), total cost, cache hit rate, and a proposed initial value for budgets.tokens_p95.
6. If v01's pass rate falls outside 50–80%, present a task-side adjustment proposal (which tasks to change and how, and the calls needed to re-run) and stop. Do not propose changing the model.
```

---

## 3. Experiment template (shared by E3-x / E4-x / E8-x)

Replace `{ID}` before use. One or two experiments per session.

```text
# Goal
Carry out experiment {ID} exactly as its entry in docs/plan/02-experiment-catalog.md specifies. Read the relevant .claude/rules/ file (pts.md / process.md / drift.md) and experiments.md before starting.

# How to proceed
1. Write the five items (hypothesis, planted condition, control, pass criteria, provenance) into docs/results/{ID}.md, and transcribe the same criteria in machine-readable form into experiments/registry.yaml. If anything differs from the catalogue, write why. Stop here and show me.
2. After approval:
   a. Implement the needed modules (following the module table in the rules; one concept per file), write unit tests that call no API, and pass `make check`
   b. Produce the replay / simulated numbers first and write them onto the result page
   c. If live numbers are needed, present the call count, the model and the rough USD, and stop. Run only after "approved"
3. Complete the result page: a provenance label on every number in the table, figures at docs/results/fig/{ID}_*.png, the verdict (PASS / FAIL / NEGATIVE) with its reason, and the findings and limitations.
4. Run `uv run agenteval verify` and confirm that the {ID} row matches the result page.
5. Update progress.md and commit.

# Discipline for verdicts
- When a criterion is unmet: if it is a flaw in the implementation or experiment design, record FAIL with the cause and a proposed fix. If the implementation is correct and the method did not work as claimed, record NEGATIVE. If you cannot tell the two apart, write "unclear" and stop
- If you feel like loosening a criterion, present a draft ADR and stop. Never edit the criteria in registry.yaml on your own
- If the control reacts, treat that as a FAIL/NEGATIVE candidate too. Do not pass an experiment by looking only at the planted side
```

---

## 4. Resume (paste at the start of a session)

```text
Continuing from last time. Do the following in order, then propose and stop:
1. Read docs/plan/progress.md, docs/plan/open-questions.md, `git log --oneline -15`, `git status`, and `uv run agenteval cost`
2. List today's items (from the first unfinished entry in progress.md, or from the "next" line if something is in flight)
3. If live is needed, give the call count and the rough USD
4. List any open questions from last time that could be resolved today
Wait for my "approved" before starting.
```

---

## 5. P5 wrap-up

```text
# Goal
Complete P5 (wrap-up).

1. Run `uv run agenteval verify`. If any PENDING remain, list them with reasons. Fill in whatever can be filled with replay / simulated. For anything needing live, give an estimate and stop
2. docs/results/summary.md is generated by verify. Do not edit it by hand
3. Write docs/results/README.md:
   - the report section ↔ experiment mapping table (verified / conditional / not verified)
   - the list of NEGATIVE and FAIL results, each with a discussion (is it a limit of the method, of the illustrative environment, or of the implementation?)
   - the limitations of this project (illustrative environment, synthetic prod, fixed judge model, live variance)
   - total cost and its ratio to budget, plus the cache hit rate
4. Review CLAUDE.md and .claude/rules/ and present, as proposals, any rules that diverged from the implementation. Do not rewrite them yourself
5. End the completion report with at most five ideas for what to do next
```

---

## 6. Quality review (optional; a few times during P1–P4)

```text
Review every result page in docs/results/ and experiments/registry.yaml, and report the following in a table. Do not fix anything.
- whether all five items are filled in
- whether every number has a provenance label, and whether any statistic mixes live and simulated
- whether the criteria in registry.yaml match the catalogue's pass criteria, and whether the verdict follows from the criteria alone
- whether the control results are written down (or whether only the planted side was reported)
- whether any path in tests/ calls the API (check `grep -rn "messages.create" tests/` and the blocking in conftest)
- whether pricing.yaml's verified_at is within 30 days
- whether the judge's model ID and call count appear on the result pages of live experiments
If there are problems, present prioritised fixes and stop.
```

---

## 7. Reminders to paste when things drift

```text
Re-read "the basic form of verification" and "absolute rules" in CLAUDE.md, then continue. In particular: write the five items first, live only after my approval, never mix provenance labels, never loosen pass criteria, and do not solve problems by switching to a stronger model.
```

```text
What you are doing now goes beyond the scope of an illustrative implementation. Readability > generality. Undo the abstraction and shrink it to the smallest implementation that can judge the catalogue's pass criteria.
```

```text
Stop before running live. Present a table with the call count, the model, the rough USD, and which numbers it is meant to produce.
```

---

## 8. Recommended order of experiments

| Order | Experiments | Why |
|---|---|---|
| 1 | E4-3, E4-1, E4-4, E4-8, E4-9 | Replay only, and they confirm corpus health (flake band, pass rate) early |
| 2 | E3-1, E3-4, E3-7, E3-3 | Replay + simulated. Settles the PTS skeleton without live |
| 3 | E3-6, E8-1, E8-8, E8-3, E8-9 | Simulated / unit / replay. Cheap |
| 4 | E3-2, E4-6, E8-2 | A little live |
| 5 | E3-5 | Branch re-execution. E4-2 and E8-5 reuse the same foundation |
| 6 | E4-2, E4-5, E4-7 | Moderate live. One experiment per session |
| 7 | E8-6, E8-4, E8-5, E8-7 | Moderate live. E8-6 uses the baseline taken in P0 |
