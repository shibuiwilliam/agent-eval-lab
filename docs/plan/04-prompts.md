# 開発・実装検証を実行するためのプロンプト集

Claude Code に貼るプロンプト。1 プロンプト = 1 セッションを基本にする（コンテキストを使い切ったら `/clear` して「再開」プロンプトを貼る。CLAUDE.md と `.claude/rules/` は再読込されるので失われない）。

## 使い方
1. シェルで `export ANTHROPIC_API_KEY=...` と `export AGENTEVAL_BUDGET_USD=120` を設定してから `claude` を起動する
2. `/context` で CLAUDE.md が Memory files に載っていることを確認する
3. 下のプロンプトを順に貼る。Claude が止まったら「承認」または「却下: 理由」で返す。承認語はこの2つに統一する
4. `/permissions` で `make check` と `uv run` 系を許可しておくと確認が減る。`rm` 系と live 実行は許可しない（都度確認する）
5. 想定セッション数: P0 = 1〜2、コーパス = 1、P1 = 3〜5、P2 = 5〜7、P3 = 4、P4 = 6〜9、P5 = 1。live の多い実験（E3-5、E4-2、E4-5、E4-7、E8-4〜E8-7）は 1 実験 1 セッション

---

## 1. P0 キックオフ

```text
# 目的
agent-eval-lab の P0（基盤）を完了させる。CLAUDE.md、docs/plan/00-plan.md、docs/plan/01-architecture.md、docs/plan/progress.md の P0 節、.claude/rules/llm-cost.md、.claude/rules/env-agent.md を読んでから始めること。

# 進め方
1. まず plan mode で P0 の実装計画を提示して止まる。含めるもの:
   - 作るファイルの一覧と、各ファイルの責務を1行ずつ
   - 単体テストの一覧（すべて API を呼ばないことを明示。conftest.py がどうやって live を遮断するかも書く）
   - タスク T-001〜T-005 の内容案（category / kind / prompt / milestones / acceptance / l_min）
   - v01_baseline のシステムプロンプト骨子（section マーカー付き）とツール 13 個の説明文の方針
   - 判断に迷う点と、その場で置く仮定（open-questions.md に書く候補）
   私が「承認」と書くまで実装しない。
2. 承認後、progress.md の P0 の項目順に実装する。項目ごとに `make check` を通し、コミットする（conventional commits）。
3. live 呼び出しは 2 回だけ。「T-001〜T-005 × v01 の記録」と「E8-6 用のモデル指紋ベースライン取得」。実行前に呼び出し数と概算 USD をまとめて提示し、私の「承認」を待つ。
4. 記録後、replay で 2 回走らせて trace ハッシュが一致することを出力で示す。一致しなければ原因（正規化漏れ、時刻依存、乱数）を特定して直す。
5. 単価は pricing.yaml から読む。verified_at が古いと思ったら公式 Pricing ページの確認を私に依頼する。

# 完了報告に含めるもの
- progress.md の更新（チェックと「次にやること」）
- `uv run agenteval cost` の出力
- open-questions.md への追記（strict ツール定義の可否、effort の API 形式、その他確認したこと）
- 実装中にレポートや CLAUDE.md と食い違った点（あれば ADR 案として提示。自分で ADR を確定しない）

# 禁止
- pytest から API を呼ぶ
- temperature / top_p / top_k を設定する
- 単価やモデル ID を models.py・pricing.yaml 以外に書く
- 私の承認前の live 実行
```

---

## 2. コーパス構築（P1 前半）

```text
# 目的
docs/plan/00-plan.md のコーパス計画と corpus.yaml に従い、タスク・版・コーパスを作る。.claude/rules/env-agent.md を読んでから始めること。

# 進め方
1. plan mode で提示して止まる:
   - タスク一覧（30 ＋ do_nothing 3 ＋ ambiguous 3 ＋ niah 4 ＋ trivial 2 ＋ private 4）。各行に id / category / kind / 1 行の内容 / l_min / risk / visibility
   - 版 v02〜v09、v01p、v02b_tone、v10_contaminated、v12_ignore_plan の planted.description と expected_effects
   - 各版のシステムプロンプトの差分方針（v01 からの差分だけ。section id を明示）
   - v06_toolschema_v2 のツール schema 差分
   「承認」まで実装しない。
2. 承認後、タスク YAML・版 YAML・プロンプトを作り、必要な検査関数を env/checks.py に追加する。全タスクの acceptance と milestones が、P0 の記録済み run または合成 trace の単体テストで少なくとも 1 回評価されることを確認する。
3. `uv run agenteval corpus build --plan corpus.yaml --dry-run` を実行し、版 × タスク × 反復の呼び出し数・トークン・USD の見積り表を提示して止まる。「承認」を待つ。
4. 承認後 `--live` で実行（並列 4）。BudgetExceeded で止まったら、部分結果と残りの見積りを報告して止まる。
5. 完了後に報告: 版ごとの合格率、v01 のフレーク帯（5 反復の合否のばらつき）、総コスト、cache hit 率、budgets.tokens_p95 の初期値案。
6. v01 の合格率が 50〜80% を外れていたら、タスク側の調整案（どのタスクをどう変えるか、再実行に必要な呼び出し数）を提示して止まる。モデルを変える提案はしない。
```

---

## 3. 実験テンプレート（E3-x / E4-x / E8-x 共通）

`{ID}` を置き換えて使う。1 セッションに 1〜2 実験。

```text
# 目的
実験 {ID} を docs/plan/02-experiment-catalog.md のエントリどおりに実施する。該当する .claude/rules/（pts.md / process.md / drift.md）と experiments.md を読んでから始めること。

# 進め方
1. 5項目（仮説・植込み条件・対照・合格基準・来歴）を docs/results/{ID}.md に書き、experiments/registry.yaml に同じ基準を機械可読で写す。カタログと違う点があれば理由を書く。ここで止まって私に見せる。
2. 承認後:
   a. 必要なモジュール（rules のモジュール表に従う。1 概念 1 ファイル）を実装し、API を呼ばない単体テストを書いて `make check` を通す
   b. 来歴が replay / simulated の数値を先に出し、結果ページに書く
   c. 来歴が live の数値が必要なら、呼び出し数・モデル・概算 USD を提示して止まる。「承認」後に実行する
3. 結果ページを完成させる: 表の各数値に来歴ラベル、図は docs/results/fig/{ID}_*.png、判定（PASS / FAIL / NEGATIVE）と理由、気づきと限界。
4. `uv run agenteval verify` を実行し、{ID} の行が結果ページと一致することを確認する。
5. progress.md を更新し、コミットする。

# 判定の規律
- 基準を満たさないとき、実装や実験設計の不備なら FAIL として原因と修正案を書く。実装が正しく手法が主張どおり働かなかったなら NEGATIVE として記録する。区別が付かないなら「不明」と書いて止まる
- 基準を緩めたくなったら ADR 案を提示して止まる。registry.yaml の criteria を勝手に書き換えない
- 対照が反応してしまった場合も FAIL/NEGATIVE の候補として扱う。植込み側だけ見て合格にしない
```

---

## 4. 再開（セッションの最初に貼る）

```text
前回の続きから。以下を順に実行してから提案して止まる:
1. docs/plan/progress.md、docs/plan/open-questions.md、`git log --oneline -15`、`git status`、`uv run agenteval cost` を読む
2. 今日やる項目（progress.md の未完了の先頭から。着手中なら「次にやること」から）を挙げる
3. live が必要なら呼び出し数と概算 USD を出す
4. 前回の open-questions で今日解決できるものがあれば挙げる
私の「承認」を待ってから着手する。
```

---

## 5. P5 総括

```text
# 目的
P5（総括）を完了させる。

1. `uv run agenteval verify` を実行し、PENDING が残っていれば一覧と理由を出す。replay / simulated で埋められるものは埋める。live が必要なものは見積りを出して止まる
2. docs/results/summary.md は verify の生成物。手で編集しない
3. docs/results/README.md を書く:
   - レポートの節 ↔ 実験の対応表（検証できた／条件付き／できなかった）
   - NEGATIVE と FAIL の一覧、それぞれの考察（手法の限界か、例示環境の限界か、実装の限界か）
   - 本プロジェクトの限界（例示環境、synthetic prod、ジャッジモデル固定、live の揺れ）
   - 総コストと予算に対する比率、cache hit 率
4. CLAUDE.md と .claude/rules/ を見直し、実装と食い違った規則を「修正案」として提示する。自分で書き換えない
5. 完了報告の最後に、次にやるなら何か（拡張案）を 5 つ以内で挙げる
```

---

## 6. 品質レビュー（任意。P1〜P4 の途中で数回）

```text
docs/results/ の全結果ページと experiments/registry.yaml をレビューし、以下を表で報告せよ。修正はしない。
- 5項目がすべて埋まっているか
- 各数値に来歴ラベルがあるか。live と simulated を混ぜた統計量がないか
- registry.yaml の criteria とカタログの合格基準が一致しているか。判定が criteria だけから導けるか
- 対照条件の結果が書かれているか（植込み側だけの結果になっていないか）
- tests/ に API を呼ぶ経路がないか（`grep -rn "messages.create" tests/` と conftest の遮断の確認）
- pricing.yaml の verified_at が 30 日以内か
- ジャッジのモデル ID と呼び出し数が live 実験の結果ページにあるか
問題があれば修正案を優先度付きで出して止まる。
```

---

## 7. 逸脱時に貼るだけの再注意

```text
CLAUDE.md の「検証の基本形」と「絶対に守ること」を読み直してから続けて。特に: 5項目を先に書く、live は私の承認後、来歴ラベルを混ぜない、合格基準は緩めない、モデルを強くして問題を解決しない。
```

```text
今の作業は例示的実装の範囲を超えている。読みやすさ ＞ 汎用性。抽象化を戻して、最小の実装でカタログの合格基準を判定できる形に縮めて。
```

```text
live を実行する前に止まって。呼び出し数、モデル、概算 USD、何の数値を得るためかを表にして提示して。
```

---

## 8. 実験の実施順（推奨）

| 順 | 実験 | 理由 |
|---|---|---|
| 1 | E4-3, E4-1, E4-4, E4-8, E4-9 | replay だけで済み、コーパスの健全性（フレーク帯、合格率）を早く確認できる |
| 2 | E3-1, E3-4, E3-7, E3-3 | replay ＋ simulated。PTS の骨格を live なしで固める |
| 3 | E3-6, E8-1, E8-8, E8-3, E8-9 | simulated / unit / replay。安価 |
| 4 | E3-2, E4-6, E8-2 | live 少量 |
| 5 | E3-5 | 分岐再実行。以降の E4-2、E8-5 が同じ基盤を使う |
| 6 | E4-2, E4-5, E4-7 | live 中量。1 実験 1 セッション |
| 7 | E8-6, E8-4, E8-5, E8-7 | live 中量。E8-6 は P0 で取ったベースラインを使う |
