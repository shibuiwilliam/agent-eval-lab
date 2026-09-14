# agent-eval-lab

レポート『AIエージェントで見落とされがちな評価手法』の 3章・4章・8章を例示的に実装し、各手法を「植え込んだ欠陥に反応し、対照には反応しない」ことで検証するプロジェクト。

## ディレクトリ構成

| パス | 内容 |
|---|---|
| `CLAUDE.md` | 作業指示（毎セッション読み込み）。`.claude/rules/` は該当ファイルを開くと読み込まれる |
| `docs/report/` | 原典レポート（`ai_agent_evaluation_report.md`） |
| `docs/plan/` | 計画（`00-plan.md`）、実験カタログ（`02-experiment-catalog.md`）、ADR（`03-decisions.md`）、進捗（`progress.md`） |
| `docs/results/` | 実験ごとの結果ページ、判定サマリ（`summary.md`）、総括（`README.md`）、live 検証レポート（`LIVE.md` / `LIVE2.md`） |
| `docs/IMPROVEMENT.md` | live 検証で見つかった欠陥・修正計画・実施結果・残作業 |
| `src/agenteval/` | 実装（`core` / `llm` / `env` / `agent` / `judge` / `pts` / `process` / `drift` / `reports`） |
| `experiments/` | 実験スクリプトとレジストリ（`registry.yaml`） |
| `tasks/` `versions/` `prompts/` | タスク定義、版定義（植込み欠陥つき）、システムプロンプト |
| `tests/` | 単体テスト（API を呼ばない。`conftest.py` が遮断する） |
| `data/` | 生成物。`data/fixtures/` 以外は git 管理外 |

ルート直下に置いているのは、標準的な配置（`README.md` / `LICENSE`）、
ツールが参照する設定（`Makefile` / `pyproject.toml` / `.gitignore` / `.env*`）、
コードが `REPO_ROOT` 直下として読む `corpus.yaml` と `pricing.yaml`、
そして Claude Code が作業ディレクトリ直下から読む `CLAUDE.md` だけです。

## セットアップ
```bash
uv sync --all-extras
export ANTHROPIC_API_KEY=...
export AGENTEVAL_BUDGET_USD=120
make check
```
