# EC-B2P コンテキスト・コスト帰属の試作 — 既存 live 軌跡での課金割り振りと台帳の照合

状態: 5 項目と方法を W0-D3（2026-11-07）で記述し、W0 の突き合わせ（同日）で SPEC.md と揃えた。**基準は確定**（G0 で承認を得る。TASK.md の草案 0.03 からの変更を含む）。結果と判定は空欄。
原典: `docs/NEW_IDEA.md` 6 章 EC-B2（理論・方法 1）、TASK.md 0.3 節 K3・K4、0.4 節 M2・M4。コーディング環境での本番は EC-B2。

## 5項目
- 仮説: (H1) `usage` の位置から割り振った課金の成分の和は、台帳（呼び出し時に `llm/cost.py` が独立に書いた実費）に一致する。(H2) 位置モデルの前提「連続するリクエストの総入力は減らない（会話は追記のみ）」が既存 live 軌跡で成り立つ。(H3) 割り振りと照合の検査は、既知の欠陥を植え込んだ記録に反応する
- 植込み条件: 既存記録の**写し**に、計測器が反応すべき欠陥を決定的に植え込む（simulated）。P2 `drop_assistant_resend` アシスタント出力の再送分を割り振りから落とす（実装の欠陥）、P3 `cache_write_as_input` キャッシュ書き込みを通常入力の単価で計算する（単価適用の欠陥）、P4 `drop_tool_result` あるステップ以降のリクエストから 1 つのツール結果を除く（履歴の切り詰め）。名前は SPEC.md 9.1 節の `AllocationPlant`・`perturb_drop_tool_result` と registry に揃えた
- 対照: 台帳（独立の記録）。植込みの無い既存 live 軌跡 240 本（`data/traces/live__*.json`）と `data/cost_ledger.jsonl` の `mode = live` の行
- 合格基準（確定）: `ledger_identity_error <= 0.001`、`allocation_identity_error <= 1e-9`、`negative_increment_rate == 0`、`ledger_match_rate >= 0.95`、`perturbation_detection_rate == 1.0`
- 来歴: 植込み以外の指標は **live（既存記録の再計算）**。`perturbation_detection_rate` は simulated（live 記録の写しに植え込んだもの）。表では列を分ける

## 方法

### 課金の割り振り（NEW_IDEA 6 章 EC-B2 の式、TASK.md M4）
- ステップ i の総入力 $T_i$ = `input_tokens` ＋ `cache_creation_input_tokens` ＋ `cache_read_input_tokens`
- 増分 $\Delta_i = T_{i+1} - T_i$。M4 に従い、直前のアシスタント出力の再送分を `output_tokens_i` で近似し、残り $\tau_i = \Delta_i - \text{output\_tokens}_i$ をステップ i のツール結果に割り当てる。文字数比による按分は照合用に併記する（記録のみ）。SPEC.md 9.1 節は増分を受け手のリクエストの番号で $\Delta_m = T_m - T_{m-1}$ と書く（本ページの $\Delta_i$ は SPEC の $\Delta_{i+1}$）。値は同じ
- 各ブロック（system＋ツール定義、初回ユーザーメッセージ、アシスタント出力の再送、各ツール結果、出力）の課金の種類は、`usage` の 3 区間（`cache_read` / `cache_creation` / `input`）とブロックの累積位置の照合で決める。オフィス環境は `history_cache: off` なので、system＋ツール定義の接頭辞より後ろはすべて `input` 単価になる（K4）
- 単価は `pricing.yaml` から読む。**割り振りの実装（W1-I1 `econ/context_cost.py`）は `llm/cost.py` の費用関数を呼ばない**。呼ぶと台帳との一致が同じ関数どうしの比較になり、単価適用の検査として意味を失う

### 台帳との対応づけ（K3）
- 台帳の行と trace のステップを `(run_id, step, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens)` の**完全一致**で対応づける。同じ鍵の行が複数あれば（同一内容の重複記録）1 行だけを使い、`duplicate_identical_rows` に数える
- 既存 240 本のうち 60 本は台帳の行がステップ数より多い（同じ run_id での再実行の記録が残っている）。鍵にトークン数を含めるので、再実行の行は対応しない。対応しなかった台帳の行は `unmatched_ledger_rows` に数える（記録のみ。K3 から 0 より大きいことが分かっている）
- `ledger_match_rate` = **すべてのステップが対応した run の数 / 240**（run 単位）。ステップ単位の対応率は記録のみ
- 恒等式の検査は、すべてのステップが対応した run だけで行う。`ledger_match_rate >= 0.95` は、恒等式が 228 本以上で検査されることを意味する

### 指標の定義
| 指標 | 定義 | 種類 |
|---|---|---|
| `ledger_identity_error` | 全ステップが対応した run について $\max_r \lvert A_r - L_r\rvert / L_r$。$A_r$ は割り振った成分の総和、$L_r$ は対応した台帳の行の `usd` の和 | 情報あり: 独立の記録との一致 |
| `allocation_identity_error` | 240 本について $\max_r \lvert A_r - C_r\rvert / C_r$。$C_r$ は trace の `usage` × 単価をステップごとに直接足した額 | **自明に近い自己検査**（下記） |
| `negative_increment_rate` | $\Delta_i < 0$ となった連続ステップ対の数 / 連続ステップ対の総数（240 本の $\sum_r (n_r - 1)$）。切り捨て・補正をする**前**の値で数える | 情報あり: 位置モデルの前提の検査 |
| `ledger_match_rate` | 上記（run 単位） | 情報あり: trace と台帳の記録の完全性 |
| `perturbation_detection_rate` | 植込み P2・P3・P4 を適用した（植込み, run）の組のうち、対応する検査が反応した割合。P2・P3 は `ledger_identity_error` の run 単位の値が 0.001 を超えたら反応、P4 は $\Delta_i < 0$ が出たら反応 | 情報あり: 検査の感度 |

- **`allocation_identity_error` は定義上ほぼ 0 になる**。割り振りは同じ `usage` の分割なので、成分の和は浮動小数点の誤差を除いて元の額に戻る。これが 0 から離れるのは、負の増分を切り捨てるなど、分割を壊す実装をしたときだけである。基準は実装の自己検査（FAIL の検出）として 1e−9 に置き、**PASS してもそれ自体は H1 の証拠にしない**。結果ページの「気づき・限界」にもこの区別を書く
- **情報があるのは**、台帳という独立の記録との一致（`ledger_identity_error`、`ledger_match_rate`）、位置モデルの前提（`negative_increment_rate`）、検査の感度（`perturbation_detection_rate`）の 4 つである
- 記録のみ: 成分ごとの費用の内訳（system＋ツール定義 / 初回ユーザーメッセージ / アシスタント再送 / ツール結果の CCA / 出力）、M4 の残り $\tau_i$ が負になる率 `m4_negative_share_rate`（再送の近似 `output_tokens` が過大な場合に出る。近似の質の指標で、基準にはしない）、文字数比の按分との差、`unmatched_ledger_rows`、`duplicate_identical_rows`、**素朴な照合**（run_id の全行を足す）での恒等式の誤差 `naive_ledger_identity_error`

### 植込みの適格条件
植込みは、その植込みが恒等式に与える相対的な影響を**植込み自体から**計算し、許容値の 2 倍（0.002）以上になる run にだけ適用する（影響が許容値未満の run で「検出しなかった」ことを見逃しと数えないため）。適格条件はデータの合否ではなく植込みの大きさだけで決まる。
P4 は 3 ステップ以上の run で、最後のリクエストからそれより前のツール結果 j を 1 つ除く。除くトークン数 $\tau_j$ が直前の増分 $\Delta_{n-2}$ を上回る（＝植込みで負の増分が生じることが植込み自体から分かる）組にだけ適用する。
適格な組の数を植込みの種類ごとに `perturbation_cases` として記録する。P4 の検出は算術的にほぼ決まっており、確かめているのは「切り捨て前に数える」という実装の約束が守られていることである。

### 許容値の根拠（到達上限）
- 書式の確認として 1 run の台帳の行を 3 行だけ見た（2026-11-07）。台帳の `usd` は行ごとに丸めずに記録されている（例: 0.0126275）。したがって $A_r$ と $L_r$ の差の下限は浮動小数点の誤差（相対 1e−12 程度）で、**到達上限は誤差 0**
- 典型的な run（K4: 1 ステップあたり `cache_read` 約 9.1k、`input` 約 610、4 ステップで約 0.02 USD）で、代表的な欠陥が恒等式に与える相対誤差の見積り:

| 欠陥 | 相対誤差の目安 | 0.03 で検出 | 0.001 で検出 |
|---|---|---|---|
| アシスタント再送の欠落（P2、4 ステップ） | 約 5.9%（再送 約 1.26k トークン × 1.00 USD/MTok） | ○ | ○ |
| 同（2 ステップ、出力 170 トークン） | 約 1% | × | ○ |
| キャッシュ書き込みを通常入力の単価で計算（P3） | 約 10.6%（9,130 × 0.25 USD/MTok） | ○ | ○ |
| 台帳の再実行行を足し込む（素朴な照合、K3 の 60 本） | 1 ステップ分以上、10% 超 | ○ | ○ |
| 丸め・浮動小数点 | 1e−12 程度 | — | — |

- TASK.md の暫定値 0.03 は 2 ステップ程度の run で再送の欠落を見逃すので、0.001 に締めた（**データを見る前の変更**。根拠は欠陥の大きさの計算だけ）。0.001 は浮動小数点の誤差に対して 9 桁の余裕がある
- `negative_increment_rate` の到達上限は 0（オフィス環境のループは履歴を落とさない）。`ledger_match_rate` の到達上限は 1.0（K3: 240 本すべてに台帳の行がある）。0.95 は、記録側の未知の欠陥（中断した run など）による除外を 12 本まで許す許容である
- **自明な実装で PASS しないこと**: run_id だけで台帳を集計する素朴な照合は、K3 の 60 本で `ledger_identity_error` が 0.001 を大きく超えて落ちる。増分を 0 で切り捨てる実装は `negative_increment_rate` を隠せるが、そのときは `allocation_identity_error` が 0 から離れる（増分は切り捨て前に数えると定義した）。何にも反応しない検査は `perturbation_detection_rate` で落ちる

### FAIL と NEGATIVE の区別（判定時の指針）
- 照合や割り振りの実装の誤り、単価の読み違いで基準を満たさないなら FAIL
- 実装が正しく、それでも負の増分が出る（例: API 側の課金区間がブロックの位置と対応しない、ツール結果の再送でトークン化が変わる）なら、位置モデルの前提が崩れたという NEGATIVE。EC-B2 の式（CCA）の適用範囲を狭める必要がある

## 結果
（未実施）

## 判定
（未実施）

## 気づき・限界
（未実施）
