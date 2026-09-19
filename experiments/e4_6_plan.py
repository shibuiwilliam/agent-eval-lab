"""E4-6 計画の外在化と乖離率（原典 4.7、ADR-005）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.process import plan as plan_mod
from agenteval.reports import plotting

METHOD = """`plan_first: true` の版（v01p と v12_ignore_plan）の run について、
提出された計画に対して DAG 検査（非巡回・`depends_on` の参照妥当性・ツール名の存在）を行い、
有効 DAG 率を出した（原典 4.7）。乖離率 δ は計画のツール名列と実行のツール名列の編集距離を
長い方で割った値（ADR-005 の近似。引数は見ない）。
乖離箇所（計画にあって実行されなかったツール、計画に無いのに実行されたツール）だけを取り出して
ジャッジに正当性を問い、「不当」と判定された割合を出した。
ジャッジは live が使えないため決定的な代替判定器（`offline_deviation_judge`）を使った。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    rows: dict[str, dict[str, Any]] = {}
    for version_id in ("v01p", "v12_ignore_plan"):
        subset = [r for r in runs if r.version_id == version_id]
        checks = [plan_mod.check_plan(r.plan) for r in subset]
        deltas = [plan_mod.deviation(r) for r in subset]
        verdicts = [plan_mod.judge_deviation(r) for r in subset if plan_mod.deviation(r) > 0]
        rows[version_id] = {
            "n": len(subset),
            "valid_rate": float(np.mean([c.valid for c in checks])),
            "delta": float(np.mean(deltas)),
            "n_deviating": len(verdicts),
            "unjustified_rate": float(np.mean([v.score <= 2 for v in verdicts]))
            if verdicts
            else 0.0,
        }

    fig = plotting.bar_compare(
        "E4-6",
        "delta",
        "計画からの乖離率 δ（ツール名列の編集距離）",
        list(rows),
        [rows[v]["delta"] for v in rows],
        "δ",
        "simulated",
    )

    metrics = {
        "plan_valid_rate_v01p": labeled(round(rows["v01p"]["valid_rate"], 4)),
        "delta_gap": labeled(round(rows["v12_ignore_plan"]["delta"] - rows["v01p"]["delta"], 4)),
        "deviation_judge_rate": labeled(round(rows["v12_ignore_plan"]["unjustified_rate"], 4)),
        "delta_v01p": labeled(round(rows["v01p"]["delta"], 4)),
        "delta_v12": labeled(round(rows["v12_ignore_plan"]["delta"], 4)),
        "n_deviating_v12": labeled(rows["v12_ignore_plan"]["n_deviating"]),
        "n_deviating_v01p": labeled(rows["v01p"]["n_deviating"]),
    }
    notes = [
        f"版ごとの結果: { {k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in rows.items()} }",
        (
            "δ はツール名列だけで測るので（ADR-005）、引数だけが違う逸脱は δ に現れない。"
            "v12 の植込みは「計画した検査ステップを実行しない」なので、ツール名列に現れて検出できる。"
        ),
        (
            "乖離正当性のジャッジは決定的な代替判定器で、「計画したステップを実行しなかった → 不当」"
            "「ツール結果にエラーがあった → 環境要因として正当」という規則で判定している。"
            "LLM ジャッジの意味的な判断力は検証していない。"
        ),
    ]
    notes.append(
        "判定は FAIL（実験設計の不備）。"
        f"δ は v01p = {rows['v01p']['delta']:.4g}、v12 = {rows['v12_ignore_plan']['delta']:.4g} と"
        "約 2 倍に開いたが、差は "
        f"{rows['v12_ignore_plan']['delta'] - rows['v01p']['delta']:.4g}"
        " で基準の 0.2 に届かなかった。原因は対照 v01p の δ が 0 でないこと。"
        "ambiguous / do_nothing のタスクでは「計画したとおりに書き込まないのが正しい挙動」なので、"
        "計画どおりに進まないことが δ に加算されてしまう。"
        "δ を normal タスクに限れば差は広がるが、それは結果を見てから対象を選び直すことになるので行わない。"
        "指標側の修正案: 正当な逸脱（ジャッジが正当と判定したもの）を δ から差し引く。"
    )
    return finalize("E4-6", metrics, METHOD, notes, figures=[("乖離率", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
