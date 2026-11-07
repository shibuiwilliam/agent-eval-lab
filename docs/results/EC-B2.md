# EC-B2 コンテキスト・コスト帰属 — ツール出力の生涯コストと履歴キャッシュの事前予測

状態: 5 項目と方法を W0-D3（2026-11-07）で記述し、W0 の突き合わせ（同日）で SPEC.md・CONTENT.md と揃えた。**基準は暫定**（恒等式の 2 つは今確定してよい。G0 の判断事項）。パイロット（W3）の後、W3-A2 で下記「パイロットで計算する量」から確定し、`registry.yaml` に固定する（ADR-033）。結果と判定は空欄。
原典: `docs/NEW_IDEA.md` 6 章 EC-B2（理論・方法 2・検証計画）、3.4 節 `history_cache`、TASK.md M4。割り振りの実装と台帳の照合は EC-B2P と共通。

## 5項目
- 仮説: (H1) 割り振った成分の和は台帳に一致する（コーディング環境の新しい live run でも）。(H2) 詳細出力の版のコスト増の大部分は `run_tests` の出力に帰属する。(H3) `history_cache` による節約は、軌跡のトークン量だけから位置モデルで予測でき、課金の実測と一致する
- 植込み条件: `c04_verbose_ctx`（`test_output_limit: null`・`test_verbosity: verbose`。コスト増）、`c01h_history_cache`（c01 に `history_cache: true` だけを足す。予測可能なコスト減）。どちらもプロンプトは c01 と同一（CONTENT.md 8.5 節）。各 10 タスク（`coding/task_sets.yaml` の `sub10`。CONTENT.md 5.7 節）× 1 反復（コーパス W4、段 `corpus`）
- 対照: (a) 同じ詳細出力を**最後のツールステップでだけ**出すスクリプト化エージェント（再送 1 回なので帰属は小さいはず。simulated）、(b) `c12_null`（記録のみ。理由は方法）
- 合格基準（暫定）: `allocation_identity_error <= 1e-9`、`ledger_identity_error <= 0.001`、`verbose_attribution_to_run_tests >= 0.8`、`history_cache_prediction_error <= 0.10`、`control_attribution <= 0.10`
- 来歴: c01・c04・c01h の usage と台帳は live。対照 (a) のスクリプト化 run は simulated（usage は sim の計数、単価は `pricing.yaml`）。事前予測の計算そのものは live の軌跡に式を当てたもの（live）。表では列を分ける

## 方法

### 割り振り
EC-B2P と同じ（位置で課金の種類を決める。M4 の按分。`llm/cost.py` を呼ばない独立実装）。c01h では会話履歴の末尾に区切りがあるので、履歴中のブロックは初回 $p_w$、以降 $p_r$ になる（NEW_IDEA 6 章の表）。
system＋ツール定義の接頭辞のトークン数 $P$ は版ごとに一定で、ステップ 0 の usage から得る。

### H2: 詳細出力の帰属
- 10 タスクそれぞれで、$D_t = \mathrm{Cost}(c04, t) - \overline{\mathrm{Cost}}(c01, t)$、$N_t = \mathrm{CCA}_{\text{run\_tests}}(c04, t) - \overline{\mathrm{CCA}}_{\text{run\_tests}}(c01, t)$。c01 側はコーパスの 6 反復の平均（10 タスク `sub10` は c01 の 24 タスク `core24` の部分集合）
- `verbose_attribution_to_run_tests` $= \sum_t N_t / \sum_t D_t$（比の和。タスク単位のクラスタ・ブートストラップで 95% 区間を記録）
- c04 では詳細出力が振る舞い（ステップ数）を変えうるので、$D_t$ には再送以外の増分も入る。比が 1 を超えることも、0.8 を下回ることもありうる。0.8 は「増分の大部分が `run_tests` 出力の生涯コスト」という主張の操作的な定義である

### H3: 履歴キャッシュの節約の予測（主指標は run 内の反実仮想）
- c01h の各 run j について、**キャッシュ関係の usage の値（`cache_creation` / `cache_read` の履歴部分）を使わず**、総入力の増分 $\Delta_i$ と出力トークン数だけから 2 通りの費用を計算する
  - $\hat C^{\text{off}}_j$: 同じ軌跡を `history_cache: off` で送った場合（接頭辞より後ろはすべて $p_{in}$）
  - $\hat C^{\text{on}}_j$: 位置モデルどおりにキャッシュが効いた場合（各ブロックは初回 $p_w$、以降 $p_r$）
  - 接頭辞（system＋ツール定義）の課金は両者で実測値を共通に使う（直前の run からの経過時間で接頭辞が温かいかが変わるが、節約の比較では打ち消す）
- 予測節約 $\hat S_j = \hat C^{\text{off}}_j - \hat C^{\text{on}}_j$、実測節約 $S_j = \hat C^{\text{off}}_j - L_j$（$L_j$ は台帳の実費）
- `history_cache_prediction_error` $= \lvert \sum_j S_j - \sum_j \hat S_j \rvert / \sum_j \hat S_j$
- **自明でないこと**: 予測にキャッシュ関係の usage を使うと、予測と実測は同じ値の言い換えになり一致は自明になる。使わないので、実際のキャッシュが位置モデルどおりに働かない場合（最小キャッシュ長、区切りの数の上限 4、区切りから遡って探す範囲の上限、有効期限 5 分、キャッシュの取りこぼし）に誤差として現れる。予測が外れた run は `cache_creation` / `cache_read` が 0 かどうかで原因を切り分けて記録する
- **なぜ run 内の反実仮想を主にするか**: c01h と c01 はモデルへの入力が同一で、違いは `cache_control` の位置だけである（モデルの振る舞いは変わらない）。したがって c01h の軌跡は c01 でも同じ確率で起こり、「同じ軌跡を off で送った費用」は正当な反実仮想になる。版の間の費用差（c01h の 10 run の平均 対 c01 の平均）は同じ期待値を持つが、軌跡の揺れで汚れる。これを `history_cache_saving_between_versions` として 95% 区間付きで記録し、**事前予測**（c01 コーパスの軌跡に $\hat S$ の式を当てた予測節約率）と並べる（記録のみ）。版間の比較を基準にしない理由は下記の検出力の計算による

### 対照 (a): 帰属が生涯（再送回数）を反映すること
- スクリプト化エージェントの対: どちらも同じタスク、同じ 12 ツールステップ＋`finish`、同じ詳細出力 1 回。E 版はステップ 1 で、L 版は最後のツールステップ（ステップ 11）で詳細出力を出す。`history_cache: off`。台本は W1-I8 が実験スクリプトの中で組む（SPEC.md 11.5 節。段 `offline`）
- `control_attribution` $= \mathrm{CCA}_L(\text{詳細出力}) / \mathrm{CCA}_E(\text{詳細出力})$。位置モデルの予測は $n_L / n_E = 1/11 \approx 0.091$
- **意味**: 出力の大きさ $\tau_i$ だけで帰属する実装（再送回数を見ない）は比が 1.0 になって落ちる。CCA が「生涯コスト」になっていることの検査である。算術でほぼ決まるので、来歴は simulated、検査としては実装の正しさの確認に近い（「気づき・限界」に書く）
- `history_cache: on` では予測比が $p_w / (p_w + 10 p_r) = 1.25 / 2.25 \approx 0.56$ になり基準を満たさない。対照は off で行う

### 対照 (b): c12_null（記録のみ）
c12 − c01 の `run_tests` 帰属の差を 95% 区間付きで記録する。「差が無い」ことを基準にすると、検出力が低いほど通りやすい（区間が広いほど 0 を含む）ので、同等性の幅を決めずに基準にはしない。W3-A2 で、同等性の幅 = MDE とした同等性検定（TOST）を基準に足すかを判断する。

### パイロットで計算する量（W3-A2）
パイロットは c01 だけ（48 タスク × 2 反復）なので、c04・c01h の値は見ない。次を計算して基準を確定する。

| 量 | 計算 | 何に使うか |
|---|---|---|
| 1 run の費用の反復間 SD $\sigma_c$ | $\hat\sigma_c^2 = \frac{1}{2T}\sum_t (x_{t1} - x_{t2})^2$（同じタスクの 2 反復の差） | H2 の分母の揺れ |
| `run_tests` 出力の切り詰め前後の大きさ | 実行器が記録する切り詰め前の文字数 `raw_chars` と表示した文字数 `shown_chars`（SPEC.md への要望）、トークン換算は c01 の usage から得る文字/トークン比 | c04 の予測コスト増 $\hat D_t = \sum_{\text{run\_tests}} (\text{raw} - \text{shown})_{\text{tok}} \cdot n_i \cdot p_{in}$ |
| H2 の検出力 | $\sum_t \hat D_t \ge 2.8 \cdot \sigma_c \sqrt{10 \cdot (1 + 1/6)}$ を満たすか（両側 5%、検出力 80%） | 満たさなければ c04 のタスク数・反復を増やす案を G2 に出す（ADR） |
| H2 の比の区間幅の見込み | デルタ法 $\mathrm{SE}(A) \approx \sqrt{\sum_t (N_t - A D_t)^2} / \lvert\sum_t D_t\rvert$ に、$N_t, D_t$ の揺れとして c01 の反復間 SD を当てた保守的な値 | 0.8 という閾値と区間幅の比較。区間幅が 0.2 を超えるなら基準を「区間の下限 ≥ 0.6」等に置き換える案を ADR で出す |
| 履歴キャッシュの予測節約の大きさ | c01 パイロットの軌跡に $\hat S$ の式を当てる | 分母 $\sum_j \hat S_j$ が小さすぎて相対誤差が不安定にならないか（1 run あたり予測節約が 0.005 USD 未満なら注意） |
| 版間比較の検出力（記録用） | 予測節約率 / （$\sigma_c$ / 平均費用 × $\sqrt{1/10 + 1/60}$） | 版間比較を基準にしない理由の数値 |
| 最小キャッシュ長 | パイロットの 1 ステップ目の総入力が Haiku 4.5 の最小キャッシュ長を超えるか | H3 の前提 |

- 恒等式の基準は EC-B2P と同じ根拠（台帳は丸めない、浮動小数点の誤差のみ）で、**今の時点で確定してよい**。暫定なのは H2・H3・対照 (a) の閾値である
- 到達上限: `allocation_identity_error` と `ledger_identity_error` は 0、`history_cache_prediction_error` はキャッシュが位置モデルどおりなら 0、`control_attribution` は 1/11。`verbose_attribution_to_run_tests` は上限が定まらない（振る舞いの変化で 1 を超えうる）

### FAIL と NEGATIVE の区別（判定時の指針）
- 割り振り・照合の実装の誤り、`history_cache` が版に正しく入っていない（W4-OB で c01h の `cache_read` が 0）は FAIL
- 実装が正しく、キャッシュが位置モデルどおりに働かない（取りこぼし、遡り範囲の上限）なら H3 は NEGATIVE（「事前予測は、この条件では当たらない」という知見）。c04 のコスト増の大半が振る舞いの変化（ステップ数の増加）によるなら H2 は NEGATIVE

## 結果
（未実施）

## 判定
（未実施）

## 気づき・限界
（未実施）
