# 進捗チェックリスト

セッション開始時にこのファイルを読み、未完了の先頭項目から着手する。完了したら `[x]` にし、結果ページや ADR へのリンクを添える。

セッションを回すためのプロンプト（P0 キックオフ／コーパス構築／実験テンプレート／再開／総括）は `04-prompts.md` にある。

## P0 基盤
- [ ] `pyproject.toml`、`Makefile`、`uv sync`、`make check` が空のパッケージで通る
- [ ] `core/schema.py`（Run / Step / Outcome / Version / Change）と `core/normalize.py`
- [ ] `llm/client.py`（record / replay / auto、`CassetteMiss`、コスト台帳、`BudgetGuard`、プロンプトキャッシュ）
- [ ] `tests/conftest.py` が `AGENTEVAL_LLM_MODE=replay` を強制し、ネットワーク呼び出しを検出したら失敗する
- [ ] `env/office.py`（SQLite、snapshot / restore / state_hash / diff / touched_resources）と `env/fixtures.py`
- [ ] `env/tools.py` 13 ツール（pydantic → JSON Schema）、`env/checks.py`、`env/external.py`
- [ ] `env/injectors.py` の骨格（Fault / Noise / Drift、manifest への記録）
- [ ] `agent/loop.py`（plan_first、並列ツール、snapshot、finish、max_steps、予算停止）
- [ ] `core/events.py` の `to_events`
- [ ] タスク T-001〜T-005、`versions/v01_baseline.yaml`、`prompts/v01_baseline.md`（section マーカー付き）
- [ ] CLI `run` / `cost`
- [ ] **DoD**: 5 タスク × v01 を live 記録 → replay 2 回で trace ハッシュ一致。`make check` 通過。E8-6 の指紋を v01 モデルで 1 回取得しておく

## P1 コーパスと過程評価の骨格
- [ ] タスク 30 ＋ edge（do_nothing 3、ambiguous 3）＋ niah 4 ＋ 自明 2 ＋ private 4
- [ ] 版 v02〜v09（各 `planted.expected_effects` 付き）と v01p / v02b_tone / v10 / v12
- [ ] `corpus.yaml` と CLI `corpus build --dry-run` → ユーザー確認 → `--live`
- [ ] ベースライン v01 の合格率が 50〜80%（外れたらタスク側で調整し、理由を記録）
- [ ] `process/metrics.py`、`process/linter.py` ＋ `rules/global.py`、`process/milestones.py`、`process/consistency.py`、`process/budget.py`
- [ ] E4-1、E4-3、E4-4、E4-8、E4-9（5項目 → 実装 → 結果ページ）
- [ ] **DoD**: 上記 5 実験が PASS / NEGATIVE で判定済み

## P2 PTS
- [ ] `pts/change.py`、`pts/coverage.py`
- [ ] `pts/impact.py`（Haiku 構造化出力）
- [ ] `pts/selector.py`（Beta 事後 → ロジスティック回帰、貪欲選択、`expected_escape`）
- [ ] `pts/pyramid.py`
- [ ] `pts/prefix_cache.py`（Checkpoint、BranchRunner、`resume_from`、`validate_branching`）
- [ ] `pts/sprt.py`（＋ Monte Carlo）
- [ ] `pts/kpi.py`
- [ ] E3-1〜E3-7
- [ ] **DoD**: 7 実験が判定済み。分岐再実行の一致率が結果ページにある

## P3 過程評価の深掘り
- [ ] `judge/verdict.py`（`submit_verdict` ツール、`tool_choice` 強制）
- [ ] `process/ablation.py`（`resume_from` を共有）
- [ ] `process/step_judge.py`（ルーブリック、参照方策一致、行動変異）
- [ ] `process/plan.py`（DAG 検査、δ、乖離正当性）
- [ ] `process/niah.py`
- [ ] E4-2、E4-5、E4-6、E4-7
- [ ] **DoD**: 4 実験が判定済み。ジャッジのモデル ID と呼び出し数が結果ページにある

## P4 陳腐化対策
- [ ] `drift/prodstream.py`（日別シフト、言い換え）
- [ ] `drift/fidelity.py`、`drift/distribution.py`、`drift/discriminative.py`
- [ ] `drift/dualtrack.py`、`drift/attribution.py`、`drift/fingerprint.py`
- [ ] `drift/prod2test.py`（サンプラー、`draft_task`、CLI `prod2test review`）
- [ ] `drift/lifecycle.py`（状態機械、CLI `lifecycle tick`）、`drift/contamination.py`
- [ ] E8-1〜E8-9
- [ ] **DoD**: 9 実験が判定済み

## P5 総括
- [ ] `reports/verify.py` と CLI `verify` → `docs/results/summary.md`
- [ ] レポートの節 ↔ 実験の対応表（検証できた／条件付き／できなかった）
- [ ] 否定的結果と限界の一覧
- [ ] 総コストの集計と予算内であることの確認
- [ ] **DoD**: PENDING が 0
