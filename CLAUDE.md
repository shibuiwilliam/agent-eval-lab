# agent-eval-lab — CLAUDE.md

レポート『AIエージェントで見落とされがちな評価手法』の 3章（PTS）・4章（過程・計画の評価）・8章（テストの陳腐化対策）を、
小さな自作エージェントと自作環境の上で**例示的に実装し、各評価手法が主張どおりに働くかを検証する**プロジェクト。
製品を作るのではない。「評価手法そのものをテストする」のが目的であり、成果物は動くコードと `docs/results/` の検証結果である。

## 毎セッション最初に読むもの
1. `docs/plan/progress.md` — 進捗チェックリスト。未完了の先頭項目から着手する
2. `docs/plan/00-plan.md` — 全体計画、フェーズ、完了条件（DoD）、予算
3. 実装する章の原典 `docs/report/ai_agent_evaluation_report.md` の該当節。式と定義はここが正
4. 実験を書くときは `docs/plan/02-experiment-catalog.md` の該当エントリ（E3-x / E4-x / E8-x）

セッションを開始／再開するときユーザーが貼るプロンプトは `docs/plan/04-prompts.md` にまとまっている。

## 検証の基本形（プロジェクトの背骨）
評価手法 M は「動いた」ではなく「**植え込んだ欠陥に反応し、対照には反応しない**」ことで検証する。
各実験は次の5項目を持つ。**実装より先に** `docs/results/E*.md` にこの5項目を書き、ユーザーに見せてから実装する。
- 仮説 H — M が何を検出または削減するか
- 植込み条件 — 反応すべき版・タスク・注入（`versions/`、`tasks/`、`env` の injector）
- 対照 — 反応してはいけない条件（ベースライン版、無関係タスク）
- 合格基準 — 数値で書く（例: 逃走欠陥率 ≤ 0.05、κ の低下 ≥ 0.3）
- 来歴 — その数値を live / replay / simulated のどれで得たか。混ぜない、必ず明記する

合格基準は後から緩めない。緩めるなら `docs/plan/03-decisions.md` に ADR を追加してから。
検証に失敗した手法は「否定的結果（NEGATIVE）」として結果ページに残す。基準に合うまで実装を弄り続けない。
新しい評価指標を足すときは、それを検出するための植込み版（`versions/`）を同時に足す。片方だけ足さない。

## 技術スタック（固定）
- macOS / Python 3.13 / `uv`（`uv sync`、`uv run`）。venv や pip を直接操作しない
- 主要依存: `anthropic`（公式 Python SDK）、`pydantic>=2`、`typer`、`numpy`、`scipy`、`pandas`、`networkx`、`scikit-learn`、`matplotlib`、`pytest`、`hypothesis`、`ruff`、`mypy`
- モデルの役割: 被験エージェント = Haiku 4.5（安価で失敗が出やすい）、ジャッジ／参照方策 = Sonnet 5、モデル入替実験 = 両者の交換
  モデル ID は `src/agenteval/llm/models.py` にのみ書く。コード中に直接書かない
- `temperature` / `top_p` / `top_k` は**設定しない**（Sonnet 5 系は非デフォルト値で 400 を返す）。決定性は record/replay カセットで担保する
- SDK の tool runner は使わない。`client.messages.create` を自前ループで回す。ステップ毎のスナップショットと分岐再実行に必要
- ジャッジの構造化出力は `submit_verdict` ツール ＋ `tool_choice` 強制で取り、本文テキストを正規表現で解析しない

## ディレクトリ（要点のみ。詳細は各ディレクトリを読めば分かる）
- `src/agenteval/{core,llm,env,agent,judge,pts,process,drift,reports}` — 章の実装は `pts/`（3章）、`process/`（4章）、`drift/`（8章）
- `tasks/*.yaml` タスク定義、`versions/*.yaml` エージェント版定義（植込み欠陥と期待効果を含む）、`prompts/` システムプロンプト
- `experiments/e3_*.py`, `e4_*.py`, `e8_*.py` — 実験スクリプト。結果は `docs/results/E*.md` と `data/results/E*.json`
- `data/` は生成物（`cassettes/`, `traces/`, `snapshots/`, `results/`, `cost_ledger.jsonl`）。`data/fixtures/` 以外は gitignore
- `.claude/rules/` — 章ごとの詳細規則。該当ファイルを開くと自動で読み込まれる（`llm-cost.md`, `env-agent.md`, `pts.md`, `process.md`, `drift.md`, `experiments.md`）

## コマンド
- `make check` — `ruff check . && isort --check-only . && ruff format --check . && mypy src && pytest -q`。コミット前に必ず通す
- `make fmt` — `ruff check --fix . && isort . && ruff format .`。整形と自動修正をまとめてかける
- `uv run agenteval run --version v01_baseline --task T-001 --mode replay|auto|live`
- `uv run agenteval corpus build --plan corpus.yaml --dry-run` → 見積りを確認してから `--live`
- `uv run agenteval exp e3_1 [--live]` — 実験を1つ実行して結果ページと JSON を更新
- `uv run agenteval verify` — 全実験の合格基準を照合し `docs/results/summary.md` に PASS/FAIL/NEGATIVE 表を出す
- `uv run agenteval cost` — 累積コストと残予算

## 絶対に守ること
- `pytest` から live API を呼ばない。`conftest.py` が `AGENTEVAL_LLM_MODE=replay` を強制し、さらに `socket.socket` を差し替えて外部接続そのものを失敗させる。run を作るテストは `LLMClient(mode="sim", ...)` を明示的に組み立てる（現状カセットには依存していない。`data/fixtures/cassettes/` は空）
- live 実行は `ANTHROPIC_API_KEY` と `AGENTEVAL_BUDGET_USD` が両方ある時だけ動く。予算超過は呼び出し前に停止する
- 50 呼び出しを超える live 実行は、先に `--dry-run` の見積りを出してユーザーに確認する
- 単価は `pricing.yaml` にのみ書く。値には確認日と公式 Pricing ページの URL を併記する
- `data/cassettes/` と `data/snapshots/` を消す・上書きする操作は先にユーザーに確認する
- API キーをコード・ログ・カセット・結果ページに残さない。カセットにはリクエスト本文と応答のみ保存する
- `src/agenteval/core/schema.py`（軌跡・スナップショット・版ハッシュ）を変えるときは移行スクリプトと ADR を同時に書く

## 開発の進め方
- 1 実験 = 1 ブランチ = 数コミット。conventional commits（`feat(pts): ...`、`exp(e3-1): ...`、`docs(results): ...`）
- 実装順は固定: 5項目を書く → オフラインで通る単体テスト（replay / simulated）→ live で少数実行 → 結果ページ → `progress.md` 更新
- シミュレーションで示せる性質（SPRT の誤り率、選択曲線）は live で再確認しない。live は「シミュレーションの入力分布が現実的か」を確かめる少数実行に限る
- ベースライン版の合格率は 50〜80% を狙う（全部通る／全部落ちるスイートは何も教えない）。外れたらタスクの難度を調整する。モデルを強くして解決しない
- 迷ったらレポートの定義に従う。レポートから逸脱するときは ADR を書く
- 大きな設計変更（スキーマ、環境、エージェントループ）は plan mode で計画を提示してから着手する

## セッションの終わり方
- `docs/plan/progress.md` のチェックを更新し、着手中の項目には「次にやること」を1行添える
- 未解決の疑問・仮定は `docs/plan/open-questions.md` に追記する（次のセッションの最初の議題になる）
- live を使ったなら `uv run agenteval cost` の出力をコミットメッセージ本文に貼る

## コード規約
- 型注釈は完全に付ける（`mypy --strict` 相当を目標）。境界は pydantic モデルで固める。`Any` を使うなら理由をコメントで書く
- 識別子は英語、docstring とコメントは日本語。結果ページと ADR は日本語
- 乱数は必ず `numpy.random.default_rng(seed)`。seed は run manifest に記録する
- LLM 呼び出しは `agenteval.llm.client.LLMClient` 経由のみ。記録／再生／コスト計測／リトライはここに集約する
- 図は matplotlib で `docs/results/fig/` に PNG 保存し、結果ページから相対パスで参照する
- 過度な抽象化をしない。例示的実装なので、読みやすさ ＞ 汎用性。一つの概念に一つのモジュール

## 用語の統一
- 軌跡 = `Run`（1タスク1版1回の実行）。ステップ = `Step`（1回の `messages.create` とそのツール実行）
- 版 = `Version`（model + system prompt + tools + config のハッシュ）。変更 = `Change`（版と版の差分、種類付き）
- 来歴ラベル = `live` / `replay` / `simulated`。結果ページの数値には必ずこれを付ける
