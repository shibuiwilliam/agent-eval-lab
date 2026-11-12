# ドキュメント索引

*English: [README.md](README.md)*

ここの文書は原則として日本語と英語の両方がある。**日本語が正路**（`foo.md`）で、英語は隣に置いた
`foo.en.md` である（例外と同期の遅れは末尾の「英語版の状態」）。プロジェクトの規則（`CLAUDE.md`、`.claude/rules/`）は日本語側のパスを参照して
おり、結果ページもそこへ生成される。

はじめて読むなら、まずリポジトリの [README.ja.md](../README.ja.md)、次に
[results/README.md](results/README.md)。

## 何を検証し、どうなったか

| 文書 | 内容 |
|---|---|
| [results/README.md](results/README.md) | **まずここ。** 結果の読み方。レポートの節 ↔ 実験の対応、否定的結果、未達の基準とその原因、循環の度合い、限界、コスト |
| [results/summary.md](results/summary.md) | 判定表。`uv run agenteval verify` の生成物。手で編集しない |
| `results/E*.md` | 実験ごとのページ。5項目・方法・来歴ラベル付きの数値・判定・気づきと限界。`uv run agenteval exp <id>` の生成物。章 C（`results/EC-*.md`）は、設計者の判定と「気づき・限界」（自動ブロックの印より上）を再実行で保持する |
| [results/LIVE.md](results/LIVE.md) | live 検証 第 1 次。sim では観測できなかった欠陥 5 件と、それが壊していたもの |
| [results/LIVE2.md](results/LIVE2.md) | live 検証 第 2 次。評価器側の欠陥と、live で否定されて取り下げた診断 1 件 |
| [REVIEW.md](REVIEW.md) | **2026-09-19 のコード・実験レビュー**: 評価器側の欠陥 8 件、判定が変わった 3 件、再実行で分かったこと |
| [PTS.md](PTS.md) | **2026-09-20〜21 の 3章（PTS）作り直し**: コールドスタートの合成変更、教師あり故障予測、ラグと変更量を特徴量にする変更履歴・テスト履歴の台帳、評価器の欠陥 5 件、3章全体の読み方 |
| [IMPROVEMENT.md](IMPROVEMENT.md) | live で見つかった欠陥、修正計画、実施結果、まだ直していないこと（R6〜R9） |

## コーディングエージェント評価（NEW_IDEA、章 C）

| 文書 | 内容 |
|---|---|
| [report/new-idea-report.md](report/new-idea-report.md) | **第 1 段の実装検証レポート**: 目的、検証の枠組み、6 実験の結果と読み方、植込みの不発、PASS の情報量、評価器の欠陥、限界、第 2 段への提言（日本語のみ） |
| [NEW_IDEA.md](NEW_IDEA.md) | コーディングエージェント評価の提案。ROI・ハーネスの効果・ボトルネック・権限とセキュリティの評価手法、原理と式、評価と推奨 3 件、第 1 段の実績。理論・式・仮説はここが正 |
| [../TASK.md](../TASK.md) | 第 1 段（推奨 3 件）の作業計画と記録。サブエージェントの役割、波（W0〜W6）、関門（G0〜G2）、費用、チェックリスト（9 章）。**第 1 段は 2026-11-08 に完了** |
| [plan/coding/SPEC.md](plan/coding/SPEC.md) | インターフェース仕様（環境・サンドボックス・コーディングループ・経済・セキュリティ・観測・CLI） |
| [plan/coding/CONTENT.md](plan/coding/CONTENT.md) | タスク内容: 6 リポジトリ、変異、ケースバンクと可視・隠しテスト、版、権限の試みのカタログ（系統と効果の名前のみ） |
| [plan/coding/THETA.md](plan/coding/THETA.md) | EC-R1 の決定ロバスト性に使うパラメータ空間 Θ |
| [plan/coding/G0.md](plan/coding/G0.md) | 関門 G0 でユーザーが判断した 15 項目 |
| `results/EC-*.md` | 章 C の 6 実験の結果ページ（EC-S1・EC-O2・EC-B2P・EC-B2・EC-R1・EC-R2） |

## 計画と設計

| 文書 | 内容 |
|---|---|
| [plan/00-plan.md](plan/00-plan.md) | 目的と非目的、検証対象の整理、コーパス計画、フェーズと完了条件、リスク |
| [plan/01-architecture.md](plan/01-architecture.md) | データモデル、実行の流れ、分岐再実行、アブレーション、CLI、ディレクトリ |
| [plan/02-experiment-catalog.md](plan/02-experiment-catalog.md) | オフィス環境の 30 実験の5項目。章 C の 6 実験の5項目は各結果ページ `results/EC-*.md` と `NEW_IDEA.md` にある。`experiments/registry.yaml` は全 36 実験を機械可読に写したもの |
| [plan/03-decisions.md](plan/03-decisions.md) | ADR-001〜ADR-046（ADR-029 以降は章 C）。「なぜこうなっているのか」を疑う前に読む |
| [plan/progress.md](plan/progress.md) | 進捗チェックリスト（P0〜P11 と NEW_IDEA 第 1 段） |
| [plan/open-questions.md](plan/open-questions.md) | 未解決の疑問とその場で置いた仮定。解決したものも消さずに残す |
| [plan/04-prompts.md](plan/04-prompts.md) | 開発セッションを回すために使ったプロンプト集 |

## 原典レポート

`report/ai_agent_evaluation_report.md` は検証対象のレポートである。本プロジェクトの**入力**であり、
式と定義はここが正なので、**意図的に日本語のみ**としている。翻訳すると「正」を争う文書が 2 つできる。
他の文書からの参照はすべて節番号（3.2、4.11、8.5 …）で行っており、これは言語によらず同じである。

## 2 言語をどう一致させているか

`results/` の結果ページは手書きではなく生成物である。

- 数値・図・判定・合格基準は `data/results/E*.json` と `experiments/registry.yaml` から来る。
  言語に依存しないので、2 言語で数値が食い違うことはない
- 言語に依存するのは文章（タイトル・5項目・方法・判定理由・気づき・図の見出し）だけで、
  その英訳は `experiments/registry.en.yaml` の 1 か所にまとめてある

したがって結果ページを直すときは、ページではなく実験スクリプトを直し、`registry.en.yaml` の
対応するエントリも直す。

```bash
uv run agenteval exp E3-1   # 実験を 1 つ再実行 → E3-1.md と E3-1.en.md を書く
uv run agenteval pages      # 既存の JSON から全ページを日英とも描き直す
uv run agenteval verify     # summary.md と summary.en.md を再生成する
```

`uv run agenteval pages` は英訳が無い実験を `missing_english` に出すので、訳し忘れが
黙って日本語のまま通ることはない。

手書きの文書（`plan/`、`IMPROVEMENT`、`results/LIVE*`、`results/README`）にはこの仕組みが無いので、
両方を編集して一致させる。

## 英語版の状態（2026-11-08）

章 C の作業は日本語で進めたので、手書きの文書の英語版に遅れがある。

| 文書 | 英語版の状態 |
|---|---|
| `results/EC-*.en.md`、`results/summary.en.md` | 生成物なので最新（`registry.en.yaml` を W6 で同期） |
| `../README.md`、`README.md`（この索引）、`results/README.en.md`、`plan/progress.en.md`、`plan/00-plan.en.md` | 2026-11-08 に章 C の状況を反映 |
| `plan/03-decisions.en.md` | ADR-001〜028 まで。**ADR-029〜046（章 C）は日本語のみ** |
| `plan/open-questions.en.md` | 2026-11-07 より前の項目まで。**章 C の項目（W1〜W6）は日本語のみ** |
| `NEW_IDEA.md`、`../TASK.md`、`plan/coding/*` | **日本語のみ**（英語版は作っていない） |

