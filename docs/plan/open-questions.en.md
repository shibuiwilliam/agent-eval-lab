# Open Questions and Assumptions

*日本語版: [open-questions.md](open-questions.md)*

Append questions that come up during a session together with the assumption made on the spot. When one is resolved, add "→ Resolved: ..." and keep it (never delete).

- [ ] Are `strict: true` tool definitions accepted by Haiku 4.5? (check in P0 and record the result as a comment in `llm/models.py`)
- [ ] The exact API shape of the `effort` parameter (confirm against the official docs before implementing the judge in P3)
- [ ] The task difficulty that puts the baseline v01 pass rate in the 50–80% range (measure and tune in P1)
- [x] Are `strict: true` tool definitions accepted by Haiku 4.5? → **Resolved (confirmed live, 2026-09-13)**. They are, but (1) every nested object needs `additionalProperties: false`, (2) 13 tools at once gives `400 Schema is too complex.`, and (3) strict suppresses v06's planted defect (recovery from a wrong argument name) on the API side, erasing the thing being observed. Therefore `USE_STRICT_TOOLS = False` stays the default, with the rationale recorded in `llm/models.py`
- [x] The exact API shape of the `effort` parameter → **Resolved (confirmed live, 2026-09-13)**. It is `output_config: {"effort": "low"}`. Sonnet 5 supports it; **Haiku 4.5 returns 400** (`This model does not support the effort parameter.`). Added to `judge_request_defaults()` along with `supports_effort(model)`
- [x] The task difficulty that puts the baseline v01 pass rate in the 50–80% range → 0.79 in sim mode. Achieved by setting `Task.sim.difficulty` to 0.3 (0.2 for deletion tasks) and by making the kinds of error per action affect acceptance
- [x] Can sim stand in for live? → **Partially (E0-1 / docs/results/LIVE.md)**. Outcome agreement 0.82, tool-sequence similarity 0.66. But `stop_appropriate` does not agree (sim 1.0 / live 0.66). **Metrics that depend on `finish` must not be discussed using sim numbers**
- [ ] Would live reach the same conclusions? Remaining: (1) the savings from branch re-execution (E3-5 is NEGATIVE in sim), (2) the stability of judge re-judging (E4-5 is pinned to 1.0 because it uses a surrogate judge), (3) model fingerprinting (E8-6 uses an artificial distribution)
- [ ] E0-1's `version_ordering_preserved` is undefined when version pass rates are tied within their confidence intervals. Should the criterion be rewritten from "orderings agree" to "the difference falls inside the confidence interval"? (this would need an ADR)
- [ ] `.claude/rules/process.md` names the rules file `rules/global.py`, but `global` is a Python keyword and cannot be imported, so it became `rules/global_rules.py`. Fix the wording in the rules, or align the implementation?
- [ ] If the redundancy-penalty similarity (`.claude/rules/pts.md`) is measured on the tool-name sequence alone, tasks of the same kind mark each other as duplicates and selection breaks (the cause of the E3-3 / E3-7 FAIL). Consider including normalised arguments, or diversify the tasks' tool sequences
