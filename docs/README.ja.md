# ドキュメント索引

*English: [README.md](README.md)*

ここの文書はすべて日本語と英語の両方がある。**日本語が正路**（`foo.md`）で、英語は隣に置いた
`foo.en.md` である。プロジェクトの規則（`CLAUDE.md`、`.claude/rules/`）は日本語側のパスを参照して
おり、結果ページもそこへ生成される。

はじめて読むなら、まずリポジトリの [README.ja.md](../README.ja.md)、次に
[results/README.md](results/README.md)。

## 何を検証し、どうなったか

| 文書 | 内容 |
|---|---|
| [results/README.md](results/README.md) | **まずここ。** 結果の読み方。レポートの節 ↔ 実験の対応、否定的結果、未達の基準とその原因、循環の度合い、限界、コスト |
| [results/summary.md](results/summary.md) | 判定表。`uv run agenteval verify` の生成物。手で編集しない |
| `results/E*.md` | 実験ごとのページ。5項目・方法・来歴ラベル付きの数値・判定・気づきと限界。`uv run agenteval exp <id>` の生成物 |
| [results/LIVE.md](results/LIVE.md) | live 検証 第 1 次。sim では観測できなかった欠陥 5 件と、それが壊していたもの |
| [results/LIVE2.md](results/LIVE2.md) | live 検証 第 2 次。評価器側の欠陥と、live で否定されて取り下げた診断 1 件 |
| [REVIEW.md](REVIEW.md) | **2026-09-19 のコード・実験レビュー**: 評価器側の欠陥 8 件、判定が変わった 3 件、再実行で分かったこと |
| [PTS.md](PTS.md) | **2026-09-20 の 3章（PTS）作り直し**: コールドスタートの合成変更、教師あり故障予測、評価器の欠陥 4 件、3章全体の読み方 |
| [IMPROVEMENT.md](IMPROVEMENT.md) | live で見つかった欠陥、修正計画、実施結果、まだ直していないこと（R6〜R9） |

## 計画と設計

| 文書 | 内容 |
|---|---|
| [plan/00-plan.md](plan/00-plan.md) | 目的と非目的、検証対象の整理、コーパス計画、フェーズと完了条件、リスク |
| [plan/01-architecture.md](plan/01-architecture.md) | データモデル、実行の流れ、分岐再実行、アブレーション、CLI、ディレクトリ |
| [plan/02-experiment-catalog.md](plan/02-experiment-catalog.md) | 全 27 実験の5項目。`experiments/registry.yaml` はこれを機械可読に写したもの |
| [plan/03-decisions.md](plan/03-decisions.md) | ADR-001〜ADR-015。「なぜこうなっているのか」を疑う前に読む |
| [plan/progress.md](plan/progress.md) | 進捗チェックリスト（P0〜P7） |
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
