---
paths:
  - "experiments/**"
  - "docs/results/**"
  - "src/agenteval/reports/**"
---

# 実験スクリプトと結果ページの規則

## レジストリ `experiments/registry.yaml`
```yaml
- id: E3-1
  chapter: 3
  title: 軌跡カバレッジ依存グラフによる候補絞り込み
  script: experiments/e3_1_coverage.py
  hypothesis: "ツール変更で実際に反転したテストはすべて候補集合に含まれ、候補は全件より小さい"
  planted: {versions: [v06_toolschema_v2], change_kind: tool}
  control: {versions: [v01_baseline]}
  criteria:
    - {metric: recall_of_flipped, op: ">=", value: 1.0}
    - {metric: candidate_ratio, op: "<=", value: 0.6}
  provenance: replay
  budget_usd: 0
```
- `agenteval verify` はこのレジストリと `data/results/E*.json` を突き合わせて PASS / FAIL / NEGATIVE / PENDING を出す。`criteria` に無い指標で合格を主張しない
- `budget_usd` は実験単体の上限。超えたら `BudgetExceeded` で止め、結果ページに「予算停止」と部分結果を書く

## 実験スクリプトの形
- `experiments/eX_Y_name.py` は `main(live: bool, seed: int) -> ExperimentResult` を持ち、CLI の `agenteval exp eX_Y` から呼ばれる。`if __name__ == "__main__"` で直接実行もできる
- 依存する run が無ければ自分で作らず、`corpus build` を促すエラーを出す（コーパスの再現性を守る）
- 結果 JSON: `{id, ran_at, seed, provenance, metrics: {...}, figures: [...], notes: [...]}`。数値には来歴ラベルを個別に付けられる（`{"value": 0.03, "provenance": "simulated"}`）

## 結果ページ `docs/results/EX-Y.md` のテンプレート
```markdown
# EX-Y タイトル

## 5項目
- 仮説:
- 植込み条件:
- 対照:
- 合格基準:
- 来歴:

## 方法
（何を、どの run 群に対して、どう計算したか。式は原典の節番号で参照）

## 結果
（表。各数値に live / replay / simulated を付ける）
![図](fig/EX-Y_xxx.png)

## 判定
PASS | FAIL | NEGATIVE（理由）

## 気づき・限界
```
- 5項目は実装前に書く。空欄のまま実装を始めない
- FAIL は「実装や実験設計の不備」、NEGATIVE は「正しく実装したが手法が主張どおり働かなかった」。区別して書く。NEGATIVE は価値ある結果として `summary.md` に載せる

## 図
- matplotlib のみ。`docs/results/fig/EX-Y_<name>.png`。DPI 150、日本語フォントは `reports/plotting.py` の設定を使う
- 1 図 1 主張。凡例に来歴ラベルを入れる（例: `simulated (n=10000)`）

## summary.md
- `agenteval verify` が生成する。手で編集しない。列: 実験 ID、章、タイトル、判定、主要指標、来歴、コスト
