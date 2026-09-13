---
paths:
  - "src/agenteval/drift/**"
  - "experiments/e8_*"
  - "tests/drift/**"
---

# 8章 テストの陳腐化対策の実装規則

原典: レポート 8.1〜8.10。ドリフトの種類（環境／タスク分布／エージェント／評価者／汚染）を混ぜて評価しない。実験ごとに「どのドリフトを起こしたか」を1つに限定する。

## モジュール構成
| ファイル | 役割 | 原典 |
|---|---|---|
| `fidelity.py` | 外部サービスのカセット TTL とライブ比較 `F` | 8.5 |
| `distribution.py` | 鮮度年齢、カテゴリ分布の JS ダイバージェンス、埋め込み MMD | 8.3 |
| `discriminative.py` | 弁別力 `d(t)` と飽和判定 | 8.4 |
| `dualtrack.py` | 模擬／ライブの合否一致 Cohen κ | 8.6 |
| `attribution.py` | 要因別入替（factorial）によるドリフト帰属 | 8.8 |
| `fingerprint.py` | 固定プローブによるモデル指紋と変化検知 | 8.8 |
| `prod2test.py` | 本番→テストのサンプリング、受入基準の起草、登録 | 8.2 |
| `lifecycle.py` | テストの状態機械と遷移条件 | 8.10 |
| `contamination.py` | カナリア走査と public/private 合格率差 | 8.9 |
| `prodstream.py` | 「本番」の代替となるセッション生成器（分布シフトと共適応の言い換えを含む） | 8.1 |

## 本番の代替（`prodstream.py`）
- 本物の本番は無いので、`ProdStream(seed, day)` が日ごとのタスク分布からセッションを生成する。日が進むと新カテゴリの比率が上がり、プロンプトが「慣れたユーザーの言い方」に寄る（Haiku で言い換えを生成し、元文も保持）
- 生成したセッションは実際に v01 で走らせて `Run` にする（live）。以降の実験はこの `Run` 群を「本番トラフィック」として使う。本番の代替であることを結果ページに明記する

## 模擬忠実度（`fidelity.py`）
- 外部サービスのカセットは `data/cassettes/external/<key>.json`。`recorded_at`, `ttl_days`, `response` を持つ
- `fidelity_job(service_now)`: 期限切れと未期限を両方ライブ比較し、`F` と「不一致キー一覧」を出す。一致判定は 8.7 の層化アサーション（厳密一致／状態検査／意味的判定）を段階的に適用し、どの層で一致したかを数える
- `DriftInjector.bump_external(v2)` の前後で `F` が下がることを植込み条件にする

## 分布距離（`distribution.py`）
- `P_suite` はタスク YAML の `category` 分布。`P_prod` は `ProdStream` の日別カテゴリ分布。JS ダイバージェンスは原典 8.3 の式
- 埋め込みは外部 API を使わず、TF-IDF（文字 2〜3-gram、日本語対応）ベクトルで MMD（RBF カーネル）を計算する。閾値はブートストラップで帰無分布を作って決める（`simulated` ラベル）
- 警報は「JS が帰無 95% 点を超えた」または「MMD の p 値 < 0.05」

## 弁別力と飽和（`discriminative.py`）
- `d(t)` は版 YAML の `quality_label`（good/bad）ごとの合格率の差。`neutral` は除外
- 飽和 = 全版で合格率 > 0.95 かつ `|d(t)| < 0.1`。飽和と判定されたテストは `lifecycle.py` に遷移イベントを送る
- 植込み: 誰でも通る自明タスクを `tasks/T-9xx` として2つ用意し、必ず飽和と判定されることを確認する

## 二重トラック（`dualtrack.py`）
- 同じタスク集合を「模擬（カセット）」と「ライブ（現在の `ExternalService`）」で走らせ、`outcome.passed` の一致を `sklearn.metrics.cohen_kappa_score` で出す
- ドリフト注入前は κ ≥ 0.6、注入後は κ の低下 ≥ 0.3 を植込み条件にする。タスク集合は `external_lookup` を必ず使うものに限定する

## ドリフト帰属（`attribution.py`）
- 要因 {model, prompt, tool} の 2^3 組合せを、接頭辞キャッシュ（3.6）を使って走らせる。各要因の主効果 = その要因を入れ替えた時の合格率変化の平均
- 予測原因 = 主効果が最大の要因。植込みでは原因を1つに固定して「予測原因 == 植込み原因」を判定する。2要因同時変更の場合は上位2つが一致するかを見る

## モデル指紋（`fingerprint.py`）
- プローブは `data/fixtures/probes.yaml` の固定 30 問（ツール無し、短答）。指標は応答長分布、先頭 20 トークンの一致率、10 個のミニタスクでのツール選択分布
- 基準分布は同一モデルで 2 回取り、差の分布を帰無とする（同一モデルでも sampling で揺れる）。検知は順列検定 p < 0.05
- 「無断更新」の代替はモデル入替（Haiku ↔ Sonnet）で行う。本物の無断更新は待てないので、これが代替であることを明記する

## 本番→テスト（`prod2test.py`）
- サンプラーは層化: 失敗 run、高コスト run（種別 p95 超）、新カテゴリを優先し、各層から等数
- 起草はジャッジに `draft_task` ツールで **タスク YAML と同じ schema** を出させる（acceptance と milestones を trace と最終状態から推定）。人の検証は CLI で1件ずつ表示して `y/n/e`（承認／却下／編集）。デモ用に `--auto-approve` を許すが結果ページには「自動承認」と書く
- 登録時に `created_at` と `origin: prod` を付け、鮮度年齢を `distribution.py` が計算する

## ライフサイクル（`lifecycle.py`）
- 状態: Created, Validated, Active, Saturated, Stale, Hardened, Rerecorded, Retired。遷移は原典 8.10 の図のとおり
- 遷移条件は関数で書き、メトリクスを引数に取る（`d(t) < 0.1 → Saturated`、`fidelity_of(t) < 0.8 → Stale` など）。状態は `data/lifecycle.jsonl` に追記型で記録する
- 単体テストで全遷移と「Retired から戻れない」ことを確認する

## 汚染（`contamination.py`）
- `visibility: private` のタスクは `canary` 文字列を prompt に含む。走査対象: `prompts/`, `versions/`, `data/cassettes/llm/` の system 部分, `docs/`
- public/private の合格率差を版ごとに出す。植込み: private タスクの canary を意図的に v01 の few-shot に混ぜた版 `v10_contaminated` を作り、走査が検出し、public/private 差が広がることを確認する

## 落とし穴
- 本番の代替は生成物である。「実運用で検証した」と書かない。結果ページの来歴は `live (synthetic prod)` とする
- ライフサイクルの遷移をメトリクス計算と同じ関数に埋め込まない。計算と遷移判断は分ける
