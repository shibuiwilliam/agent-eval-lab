---
paths:
  - "src/agenteval/pts/**"
  - "experiments/e3_*"
  - "tests/pts/**"
---

# 3章 Predictive Test Selection の実装規則

原典: レポート 3.1〜3.8。式番号ではなく節番号で参照する。

## モジュール構成（1概念1ファイル）
| ファイル | 役割 | 原典 |
|---|---|---|
| `change.py` | `Change(kind, base, target, components)`。`kind ∈ {prompt, model, tool, config, fixture}`。プロンプト差分は section マーカー単位、ツール差分はツール名単位、モデル・設定は「全体」 | 3.1 |
| `coverage.py` | trace から `U(t)`（呼んだツール、参照した prompt section、使ったフィクスチャ要素）を抽出し、`candidates(change)` を返す | 3.2 |
| `impact.py` | プロンプト diff → LLM（Haiku、構造化出力）で影響タグ列挙 → タスクの tags と TF-IDF 類似で照合 | 3.3 |
| `selector.py` | `p̂_t` 推定、エントロピー、コスト、冗長性ペナルティ付き貪欲選択 | 3.4 |
| `pyramid.py` | `decide_level(change) -> Level(L0..L5)` の規則 | 3.5 |
| `prefix_cache.py` | チェックポイント保存と `BranchRunner` | 3.6 |
| `sprt.py` | `SPRT(p0, p1, alpha, beta)` と Monte Carlo 検証 | 3.7 |
| `kpi.py` | 逃走欠陥率、ε-探索、不可侵集合 | 3.8 |

## p̂_t の推定（`selector.py`）
- 履歴が乏しい前提で設計する。既定は Beta 事後: `p̂ = (passes + 1) / (passes + fails + 2)`。同一版内の反復は1件ではなく反復数ぶん数える
- 変更イベントが 3 件以上たまったら、特徴量（直近合格率、反転回数、スコア余裕、カバレッジ重なり `|U(t) ∩ C(Δ)|`、意味的類似度、フレーク率）でロジスティック回帰を学習し、Beta 事後と平均する。学習・評価は**変更イベント単位の leave-one-out**（同じ変更の run を訓練と評価に跨がせない）
- フレーク率はベースライン版を 3 回走らせて先に測る。フレークの範囲内の反転は「反転」と数えない
- 優先度は `H(p̂_t) / c(t)`。`c(t)` は過去 run の平均トークン。選択は降順貪欲で、`max_sim(t, S) > 0.8` の候補は飛ばす。`sim` はツール名列の正規化編集距離の補数
- 出力は `Selection(selected, skipped, budget, expected_escape)`。`expected_escape` は `Σ_{t∉S} (1 − p̂_t)` を全体で割った値で、実測の逃走欠陥率と並べて報告する

## 接頭辞キャッシュと分岐再実行（`prefix_cache.py`）
- チェックポイント = `Checkpoint(run_id, step_i, messages_prefix, snapshot_ref, action_norm)`。`messages_prefix` はステップ i 開始時点の会話全体（旧版の assistant 発話を含む）
- `BranchRunner.run(new_version, base_run)`:
  1. i = 0 から順に `messages_prefix` を新版の system prompt とツールで `create` し、返った行動を `normalize_args` で旧行動と比較
  2. 一致すれば次へ（この呼び出しは新版の判断確認であり live コストがかかる。旧版と同一の版ならカセットに当たり無料）
  3. 不一致なら `divergence_step = i` を記録し、`snapshot_ref` を restore してそこから通常ループで最後まで走る
- 同値判定は既定で「ツール名 ＋ 正規化引数の完全一致」。`--semantic-equiv` を付けた時だけジャッジで意味的同値を問う。どちらを使ったか manifest に残す
- 妥当性検査 `validate_branching(sample_n)`: 同じ (task, version) を分岐再実行と通常実行の両方で走らせ、`outcome.passed` の一致率と `divergence_step` の分布を出す。一致率が 0.8 を下回るなら手法として NEGATIVE
- 節約量の指標は「live で実行したステップ数 ÷ 通常実行の総ステップ数」。判断確認の呼び出しも live コストなので、トークンベースの節約率も併記する

## SPRT（`sprt.py`）
- `update(success: bool) -> Decision(continue | accept | reject)`。対数尤度比は原典 3.7 の式そのまま
- Monte Carlo 検証は API 不要: `p_true` を格子で振り、経験的 α・β と平均試行回数を固定 n 法と比較する。結果は `simulated` ラベル
- live は境界テスト（Beta 事後が 0.3〜0.7）を 5 件選び、決着までの試行回数だけを確認する

## 落とし穴
- カバレッジは replay trace からも live trace からも同一に取れる。カバレッジ抽出のために live を走らせない
- 「変更していないのに壊れる」（外部環境ドリフト）は PTS の対象外。8章に送る。混ぜて評価しない
- 不可侵集合（`risk: inviolable`）は選択ロジックの外で必ず実行する。選択器のテストで不可侵集合が落ちていたら実装バグ
