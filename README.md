# agent-eval-lab

レポート『AIエージェントで見落とされがちな評価手法』の 3章・4章・8章を例示的に実装し、各手法を「植え込んだ欠陥に反応し、対照には反応しない」ことで検証するプロジェクト。

- 作業指示: `CLAUDE.md`（毎セッション読み込み）と `.claude/rules/`（該当ファイルを開くと読み込まれる）
- 計画: `docs/plan/00-plan.md`、実験カタログ: `docs/plan/02-experiment-catalog.md`、進捗: `docs/plan/progress.md`
- 原典: `docs/report/ai_agent_evaluation_report.md`

## セットアップ
```bash
uv sync --all-extras
export ANTHROPIC_API_KEY=...
export AGENTEVAL_BUDGET_USD=120
make check
```
