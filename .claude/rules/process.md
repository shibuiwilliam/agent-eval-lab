---
paths:
  - "src/agenteval/process/**"
  - "src/agenteval/judge/**"
  - "experiments/e4_*"
  - "tests/process/**"
---

# 4章 過程・計画の評価の実装規則

原典: レポート 4.1〜4.11。指標の定義はレポートの表 4.2 と各節の式に従う。

## モジュール構成
| ファイル | 役割 | 原典 |
|---|---|---|
| `metrics.py` | 迂回率、重複呼び出し率、後戻り回数、コンテキスト増加量、検証行動率、復帰成功率、停止適切性、質問適切性 | 4.2 |
| `ablation.py` | ツール結果を消して接尾辞を再実行し、無駄呼び出し率 `U(τ)` を出す | 4.3 |
| `linter.py` | 時相論理アサーション DSL と規則集 | 4.4 |
| `milestones.py` | 半順序マイルストーンの到達判定と部分点 | 4.5 |
| `step_judge.py` | ルーブリック判定（Sonnet）と参照方策一致 `A(τ)` | 4.6 |
| `plan.py` | 計画 DAG の検査と乖離率 `δ` | 4.7 |
| `niah.py` | 軌跡内 NIAH の想起率 `R(n)` | 4.9 |
| `consistency.py` | pass@k、pass^k、ステップ数分散、行動エントロピー | 4.10 |
| `budget.py` | 種別ごとの p95 予算とペア指標 | 4.11 |

## 指標の計算規則（`metrics.py`）
- すべて `Run` だけから決定的に計算する。LLM を呼ぶ指標は `step_judge.py` と `ablation.py` に隔離する
- `L_min` はタスク YAML の `l_min`、無ければコーパス内の成功 run の最短ステップ数。どちらを使ったかを結果に書く
- 重複呼び出しは `(name, args_norm)` の完全一致。読み直し（`file_read` の同一 path）は後戻りにも数える
- 検証行動率の分母は「書込み系ツールを1回以上呼んだ run」。読むだけのタスクでは未定義（NaN）にする
- 質問適切性は `kind: ambiguous` では「finish 前に質問して止まったか」、`kind: normal` では「質問せずに完了したか」で判定する

## 軌跡リンター DSL（`linter.py`）
- イベント列は `core/events.py` の出力。DSL は Python の関数合成で、パーサを作らない
- 演算子: `always(pred)`, `once_before(target, required)`（target の各出現より前に required が一度はある）, `not_until(forbidden, required)`, `count_le(pred, n)`, `next_after(trigger, allowed)`, `eventually(pred)`
- 規則は `Rule(id, description, check: Callable[[list[Event]], list[Violation]])`。グローバル規則集 `rules/global.py` とタスク固有規則（YAML の `forbidden`）を合成する
- 最初に入れるグローバル規則: `claim_done_requires_checks`, `no_delete_without_backup`, `read_before_edit`, `no_repeat_call_gt2`, `write_within_scope`, `error_then_retry_le2_or_escalate`
- 単体テストは手書きイベント列で全演算子を網羅し、hypothesis で「delete の直前に backup を挿入した列は `no_delete_without_backup` に違反しない」のような性質を検査する
- リンター違反は `Outcome.linter_violations` に入れ、`passed` とは独立に扱う（違反があっても acceptance は通り得る。それが 4.1 の右上象限）

## アブレーション（`ablation.py`）
- 対象は「サンプルした run × その全ツール呼び出し」。既定は 5 run。全 run に対して回さない
- 手順: 呼び出し i の `tool_result.content` を `"[result omitted by ablation]"` に置換した会話でステップ i+1 から live 実行（環境はステップ i 後のスナップショットを restore）→ `outcome.passed` を比較
- 代理指標: Sonnet に「この結果はその後の判断で参照されたか」を `submit_verdict` で問う。アブレーション結果を正解として precision / recall を出す
- 来歴はアブレーション本体が `live`、代理指標の校正も `live`。数を混ぜない

## ステップ単位判定（`step_judge.py`）
- ルーブリック判定: 入力は「状態の要約（直近 k ステップのイベント ＋ 環境 diff）＋ 当該行動」。自己申告文は除外する（5.2 の主張と証拠の分離）。出力は `score ∈ {1..5}`, `reason`, `evidence_refs`
- 参照方策一致: 同じ `messages_prefix` を参照モデル（Sonnet）に K=5 回投げ、行動の同値類（ツール名 ＋ 正規化引数）を数えて `π̂_ref([a_i] | s_i)` を得る。集約は平均と最小値の両方
- 検証は行動変異で行う: 正常 run のステップの行動を「誤ツール」「誤引数」「破壊的操作」に置換した合成ステップを作り、スコアが元より下がる割合を測る（合格基準は実験カタログ）

## 計画（`plan.py`）
- 実行前検査: DAG 非循環（networkx）、`depends_on` の参照妥当性、存在しないツール名の有無、要件網羅（ジャッジに「各要件に対応するステップがあるか」を問う。任意）
- 乖離率 `δ` は「計画のツール名列」と「実行のツール名列」の編集距離を長い方で割る。引数は見ない（粒度は原典 4.7 の意味的同値に合わせ、ここではツール名で近似する。ADR に記載済み）
- 乖離箇所だけを抽出し、ジャッジに「正当な変更か」を問う。全体を読ませない

## 軌跡内 NIAH（`niah.py`）
- `kind: niah` のタスクは `key_fact` と `n_noise` をパラメータに持つ。`NoiseInjector` がステップ k 以降 n ステップの応答に無関係な長文を付加する
- 最終判断が `key_fact` に依存する acceptance を持つ。`R(n)` は n ∈ {0, 2, 4, 8} で、版 v01（raw）と v08（summarize）の両方で測る
- 曲線と AUC を図にする。単調減少でなければ「ノイズが効いていない」疑いとして注入量を見直す（結果を捨てない、記録する）

## 一貫性とコスト（`consistency.py`, `budget.py`）
- pass@k と pass^k は原典 4.10 の式。n=5 反復を 10 タスクで取る。k=1..3
- p95 予算はベースライン版の種別ごとの p95 × 1.5 を初期値として `corpus.yaml` に書く。超過は `verify` で FAIL 扱い
- Goodhart のペア指標は必ず同じ表に並べる: ステップ数↔検証行動率、トークン↔NIAH 想起率、質問回数↔誤前提率、完了時間↔スコープ違反数

## 落とし穴
- 推論トレース（思考）を指標に使わない。行動と環境 diff だけを見る（原典 4.8）
- 「効率が上がった」だけの結果を書かない。ペア指標が横ばいか上がっていることを同時に示す
- ジャッジのモデルは固定し、結果ページにモデル ID を書く。途中でジャッジを変えたら全実験を再判定する
