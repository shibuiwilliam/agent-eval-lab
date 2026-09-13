# 進捗チェックリスト

セッション開始時にこのファイルを読み、未完了の先頭項目から着手する。完了したら `[x]` にし、結果ページや ADR へのリンクを添える。

セッションを回すためのプロンプト（P0 キックオフ／コーパス構築／実験テンプレート／再開／総括）は `04-prompts.md` にある。

## P0 基盤
- [x] `pyproject.toml`、`Makefile`、`uv sync`、`make check` が空のパッケージで通る
- [x] `core/schema.py`（Run / Step / Outcome / Version / Change）と `core/normalize.py`
- [x] `llm/client.py`（record / replay / auto / **sim**、`CassetteMiss`、コスト台帳、`BudgetGuard`、プロンプトキャッシュ）
- [x] `tests/conftest.py` が `AGENTEVAL_LLM_MODE=replay` を強制し、ソケットも塞ぐ
- [x] `env/office.py`（SQLite、snapshot / restore / state_hash / diff / touched_resources）と `env/fixtures.py`
- [x] `env/tools.py` 13 ツール（pydantic → JSON Schema）、`env/checks.py`、`env/external.py`
- [x] `env/injectors.py`（Fault / Noise / Drift、manifest への記録）
- [x] `agent/loop.py`（plan_first、並列ツール、snapshot、finish、max_steps、予算停止、分岐再開）
- [x] `core/events.py` の `to_events`
- [x] タスク T-001〜T-005、`versions/v01_baseline.yaml`、`prompts/v01_baseline.md`（section マーカー付き）
- [x] CLI `run` / `cost`
- [x] **DoD（条件付き）**: live が使えないため sim モードで代替（ADR-008）。同一条件で 2 回実行して trace ハッシュ一致を確認。`make check` 通過。E8-6 の live 指紋は**未取得**（API キーが無い）

## P1 コーパスと過程評価の骨格
- [x] タスク 46 件（normal 30 ＋ do_nothing 3 ＋ ambiguous 3 ＋ niah 4 ＋ 自明 2 ＋ private 4）
- [x] 版 13（v01〜v09 ＋ v01p / v02b_tone / v10 / v12）。各 `planted.expected_effects` 付き
- [x] `corpus.yaml` と CLI `corpus build --dry-run`（live 見積り）→ `--sim` で生成（ADR-008 / ADR-010）
- [x] ベースライン v01 の合格率 0.79（目標 50〜80%。タスク側の difficulty と誤りの種類で調整）
- [x] `process/metrics.py`、`process/linter.py` ＋ `rules/global_rules.py`、`process/milestones.py`、`process/consistency.py`、`process/budget.py`
- [x] E4-1、E4-3、E4-4、E4-8、E4-9
- [x] **DoD**: 上記 5 実験が判定済み

## P2 PTS
- [x] `pts/change.py`、`pts/coverage.py`
- [x] `pts/impact.py`（構造化出力の経路は実装済み。live 不可のため決定的抽出で実行）
- [x] `pts/selector.py`（Beta 事後 → ロジスティック回帰、貪欲選択、`expected_escape`）
- [x] `pts/pyramid.py`
- [x] `pts/prefix_cache.py`（Checkpoint、BranchRunner、`resume_from`、`validate_branching`）
- [x] `pts/sprt.py`（＋ Monte Carlo）
- [x] `pts/kpi.py`
- [x] E3-1〜E3-7（PASS 3 / NEGATIVE 2 / FAIL 2）
- [x] **DoD**: 7 実験が判定済み。分岐再実行の一致率 1.0（60 組）が E3-5 に記載

## P3 過程評価の深掘り
- [x] `judge/verdict.py`（`submit_verdict` ツール、`tool_choice` 強制）＋ `judge/rubric.py`（決定的代替判定器）
- [x] `process/ablation.py`（`resume_from` を共有）
- [x] `process/step_judge.py`（ルーブリック、参照方策一致、行動変異）
- [x] `process/plan.py`（DAG 検査、δ、乖離正当性）
- [x] `process/niah.py`
- [x] E4-2、E4-5、E4-6、E4-7（PASS 1 / FAIL 3）
- [x] **DoD（条件付き）**: 4 実験が判定済み。**ジャッジは live ではなく決定的代替**なので、モデル ID の代わりに `offline-rubric` を記録している

## P4 陳腐化対策
- [x] `drift/prodstream.py`（日別シフト、言い換え、新カテゴリの実行可能タスク）
- [x] `drift/fidelity.py`、`drift/distribution.py`、`drift/discriminative.py`
- [x] `drift/dualtrack.py`、`drift/attribution.py`、`drift/fingerprint.py`
- [x] `drift/prod2test.py`（層化サンプラー、`draft_task`、CLI `prod2test`）＋ `drift/draft.py`
- [x] `drift/lifecycle.py`（状態機械、CLI `lifecycle`）＋ `drift/state_source.py`、`drift/contamination.py`
- [x] E8-1〜E8-9（PASS 6 / NEGATIVE 1 / FAIL 2）
- [x] **DoD**: 9 実験が判定済み

## P5 総括
- [x] `reports/verify.py` と CLI `verify` → `docs/results/summary.md`（PASS 15 / NEGATIVE 3 / FAIL 7 / PENDING 0）
- [x] レポートの節 ↔ 実験の対応表 → `docs/results/README.md`
- [x] 否定的結果と限界の一覧 → `docs/results/README.md`

## P6 live 検証（実 API）
- [x] `.env` の `ANTHROPIC_API_KEY` を使い、`AGENTEVAL_BUDGET_USD=20` で live 実行
- [x] 未解決だった open-questions 2 件（`strict` ツール、`effort`）を実測で確定
- [x] live でしか見えない欠陥 5 件を特定（`IMPROVEMENT.md` L1〜L5）
- [x] 修正を適用（`contacts_search` 追加、プロンプト強化、リンター規則、数値正規化、シミュレータ修正）
- [x] sim の妥当性を測る実験 E0-1 を追加し、sim 修正の前後で比較
- [x] sim コーパス再生成 → 26 実験を再実行 → `docs/results/LIVE.md` に記録
- [x] **DoD**: live 呼び出し 354 回・0.81 USD。PASS 14 / NEGATIVE 3 / FAIL 9 / PENDING 0

## P7 live 検証 第 2 次（R1〜R5）
- [x] R1 ジャッジを live に → 新実験 E0-2（NEGATIVE: 代替判定器は live と一致しない／live ジャッジは安定）
- [x] R2 モデル指紋を live に → 改訂前の合成統計量は検知できず。ADR-013 で応答長だけの検定に
- [x] R3 E0-1 の基準は**書き直さず**、比較対象に v02 を加えて順序を検証できるようにした（ADR-015）
- [x] R4 冗長性類似度の改訂は **live で前提が否定されて取り下げ**（ADR-014）
- [x] R5 live 反復を 3 → 6、版を 3 → 4（240 run）
- [x] 新規 L6: 植込み版 v02 の欠陥が実モデルで再現していなかった → プロンプトを立て直し live で確認
- [x] **DoD**: live 1,308 呼び出し / 3.09 USD。PASS 14 / NEGATIVE 4 / FAIL 9 / PENDING 0（27 実験）

## 次にやること
- `IMPROVEMENT.md` の「11. まだ直していないこと」の R6〜R9。
  最優先は R6（E4-2 / E4-6 を live ジャッジで取り直す、見積り 0.3 USD）
- [ ] 総コストの集計と予算内であることの確認
- [ ] **DoD**: PENDING が 0
