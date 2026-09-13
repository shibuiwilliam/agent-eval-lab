# 全体計画 — 評価手法の例示的実装検証

## 目的と非目的
**目的**: レポート 3章（PTS）・4章（過程・計画の評価）・8章（テストの陳腐化対策）の手法を、制御可能な小さなエージェントと環境の上で実装し、「植え込んだ欠陥に反応し、対照には反応しない」ことを数値で確かめる。副産物として、レポートの主張のうち成り立たなかったものを否定的結果として記録する。

**非目的**: 本番品質の評価基盤、汎用フレームワーク、UI、マルチエージェント、5〜7章の網羅（ジャッジは 4章の検証に必要な最小限だけ実装する）。

## 検証対象の整理

| 章 | 手法 | 実験 | 主な来歴 |
|---|---|---|---|
| 3 | 軌跡カバレッジ依存グラフ | E3-1 | replay |
| 3 | 意味的影響推定 | E3-2 | live（Haiku 少量） |
| 3 | 不確実性駆動選択 | E3-3 | replay ＋ simulated |
| 3 | テストピラミッドの段判定 | E3-4 | replay |
| 3 | 接頭辞キャッシュと分岐再実行 | E3-5 | live |
| 3 | SPRT 早期打切り | E3-6 | simulated ＋ live 少量 |
| 3 | 逃走欠陥率・ε-探索・不可侵集合 | E3-7 | replay |
| 4 | 軌跡メトリクス | E4-1 | replay |
| 4 | アブレーション無駄判定 | E4-2 | live |
| 4 | 軌跡リンター | E4-3 | replay |
| 4 | 半順序マイルストーン | E4-4 | replay |
| 4 | ステップ単位判定 | E4-5 | live（Sonnet） |
| 4 | 計画の外在化と乖離率 | E4-6 | replay ＋ live 少量 |
| 4 | 軌跡内 NIAH | E4-7 | live |
| 4 | pass@k / pass^k と一貫性 | E4-8 | replay |
| 4 | コスト予算と Goodhart ペア | E4-9 | replay |
| 8 | 模擬忠実度と TTL | E8-1 | replay ＋ 外部サービス比較 |
| 8 | 鮮度と分布距離 | E8-2 | simulated ＋ synthetic prod |
| 8 | 弁別力と飽和 | E8-3 | replay |
| 8 | 二重トラック κ | E8-4 | live |
| 8 | ドリフト帰属 | E8-5 | live（分岐再実行） |
| 8 | モデル指紋 | E8-6 | live |
| 8 | 本番→テスト・パイプライン | E8-7 | live（synthetic prod） |
| 8 | ライフサイクル状態機械 | E8-8 | unit（API 不要） |
| 8 | 汚染検知 | E8-9 | replay |

詳細は `02-experiment-catalog.md`。

## コーパス計画（`corpus.yaml`）
- タスク 30（scheduling 10 / mail 8 / files 8 / mixed 4）＋ edge（do_nothing 3、ambiguous 3）＋ niah 4 ＋ 自明タスク 2 ＋ private 4
- 版 9（v01〜v09）＋ 汚染版 v10。反復はベースライン 5、その他 3
- 概算呼び出し数: 30 × 9 × 3 ≈ 810 run × 平均 7 ステップ ≈ 5,700 呼び出し（Haiku）。ジャッジ・参照方策（Sonnet）は E4-2, E4-5, E8-7 で合計 1,500 呼び出し以内
- 概算コスト: Haiku 側 20〜30 USD、Sonnet 側 20〜40 USD。**全体予算 120 USD**、`AGENTEVAL_BUDGET_USD` に設定。実験ごとの上限は `registry.yaml`
- 単価は `pricing.yaml`。プロンプトキャッシュで入力コストは概算より下がる見込み。見積りは `corpus build --dry-run` で必ず更新する

## ベースラインの難度調整
ベースライン v01 の合格率が 50〜80% になるようタスクを調整する。全部通る／全部落ちるスイートでは、PTS も弁別力も検証できない。調整はタスク側（曖昧さ、必要ステップ数、障害注入）で行い、モデルを変えて解決しない。

## フェーズと完了条件（DoD）

### P0 基盤
- `LLMClient`（record/replay、コスト台帳、BudgetGuard、プロンプトキャッシュ）
- `office` 環境（SQLite、snapshot/restore/hash/diff）、ツール 13 個、注入器の骨格
- エージェントループ、`Run` スキーマ、`to_events`
- タスク 5 個、v01_baseline、CLI `run`
- **DoD**: 5 タスク × v01 を live で 1 回ずつ記録し、replay で 2 回走らせて trace ハッシュが一致する。`make check` が通る。`pytest` が API 無しで通る

### P1 コーパスと過程評価の骨格
- タスク 30 ＋ edge、版 v01〜v09、`corpus build`（dry-run → live）
- `metrics.py`, `linter.py`, `milestones.py`, `consistency.py`, `budget.py`
- **DoD**: E4-1, E4-3, E4-4, E4-8, E4-9 が PASS または NEGATIVE で結果ページあり。ベースライン合格率が 50〜80% に入っている

### P2 PTS
- `change.py`, `coverage.py`, `impact.py`, `selector.py`, `pyramid.py`, `prefix_cache.py`, `sprt.py`, `kpi.py`
- **DoD**: E3-1〜E3-7 が判定済み。分岐再実行の妥当性検査（一致率）が結果ページにある

### P3 過程評価の深掘り
- `ablation.py`, `step_judge.py`, `plan.py`, `niah.py`
- **DoD**: E4-2, E4-5, E4-6, E4-7 が判定済み。ジャッジのモデル ID と件数が結果ページにある

### P4 陳腐化対策
- `prodstream.py`, `fidelity.py`, `distribution.py`, `discriminative.py`, `dualtrack.py`, `attribution.py`, `fingerprint.py`, `prod2test.py`, `lifecycle.py`, `contamination.py`
- **DoD**: E8-1〜E8-9 が判定済み

### P5 総括
- `agenteval verify` で `summary.md` を生成。否定的結果と限界の一覧。レポートの各節に対する「検証できた／できなかった／条件付き」の対応表
- **DoD**: 全実験が PASS / FAIL / NEGATIVE のいずれかで、PENDING が無い。総コストが予算内

## リスクと対策
| リスク | 対策 |
|---|---|
| Haiku がタスクを解けなさすぎる／解けすぎる | 難度調整はタスク側。合格率 50〜80% を P1 の DoD に含める |
| プロジェクト中にモデルが更新される | モデル ID を固定し、E8-6 の指紋を P0 で先に1回取っておく。変化があれば ADR |
| レート制限 | 並列 4 まで。バッチは独立呼び出しにだけ |
| replay の期待とズレ（同一リクエストでも live は揺れる） | replay は決定性のため、live の揺れは反復で扱う。両者を混ぜて統計を取らない |
| 検証がうまくいかず基準を緩めたくなる | 基準変更は ADR 必須。NEGATIVE を正当な結果として扱う |
| カセット・スナップショットの肥大化 | `data/` は gitignore。`agenteval data prune --older-than` を用意する |

## プロジェクト全体の成功基準
- 25 実験すべてが判定済み（PENDING なし）
- 各章で少なくとも 1 つの「否定的結果または条件付き」が誠実に記録されている（全部 PASS は疑う）
- 総コストが 120 USD 以内
- レポートの節と実験の対応表が `docs/results/summary.md` にある
