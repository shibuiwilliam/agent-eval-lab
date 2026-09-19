"""E4-9 コスト予算と Goodhart ペア指標（原典 4.11）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task
from agenteval.process import budget as budget_mod
from agenteval.process import metrics as metrics_mod
from agenteval.reports import plotting

VERSIONS = ["v01_baseline", "v04_loopy", "v07_step_minimizer"]

METHOD = """v01_baseline の run から種別（category）ごとのトークン p95 × 1.5 を予算の初期値として算出し
（原典 4.11）、各版の run が予算を超えるかを判定した。
Goodhart のペアは「ステップ数 ↔ 検証行動率」を同じ表に並べる。
v07_step_minimizer はステップ数最小化を指示した版、v04_loopy は同じ検索をなぞる版である。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    v01 = [r for r in runs if r.version_id == "v01_baseline"]
    budgets = budget_mod.p95_by_category(v01, factor=1.5)

    rows = {}
    for version in VERSIONS:
        subset = [r for r in runs if r.version_id == version]
        results = budget_mod.check_budgets(subset, budgets)
        rows[version] = budget_mod.exceed_rate(results)

    per_version = {
        version: [
            metrics_mod.compute(r, l_min=get_task(r.task_id).l_min)
            for r in runs
            if r.version_id == version
        ]
        for version in VERSIONS
    }
    table = budget_mod.goodhart_table([m for rows_ in per_version.values() for m in rows_])

    steps_v01 = table["v01_baseline"]["steps"]
    steps_v07 = table["v07_step_minimizer"]["steps"]
    ver_v01 = table["v01_baseline"]["verification_rate"]
    ver_v07 = table["v07_step_minimizer"]["verification_rate"]

    fig = plotting.bar_compare(
        "E4-9",
        "goodhart",
        "Goodhart のペア: ステップ数（左軸の比）と検証行動率",
        [f"{v}\nsteps" for v in VERSIONS] + [f"{v}\nverify" for v in VERSIONS],
        [table[v]["steps"] / steps_v01 for v in VERSIONS]
        + [table[v]["verification_rate"] for v in VERSIONS],
        "v01 比 / 検証行動率",
        "simulated",
    )

    metrics = {
        "steps_ratio_v07": labeled(round(steps_v07 / steps_v01, 4)),
        "verification_ratio_v07": labeled(round(ver_v07 / ver_v01, 4) if ver_v01 else 0.0),
        "p95_exceed_v04": labeled(int(rows["v04_loopy"]["rate"] > 0)),
        "p95_exceed_v01": labeled(int(rows["v01_baseline"]["rate"] > 0)),
        "exceed_rate_v04": labeled(rows["v04_loopy"]["rate"]),
        "exceed_rate_v01": labeled(rows["v01_baseline"]["rate"]),
        "exceed_rate_v07": labeled(rows["v07_step_minimizer"]["rate"]),
        "pass_rate_v07": labeled(round(table["v07_step_minimizer"]["pass_rate"], 4)),
        "pass_rate_v01": labeled(round(table["v01_baseline"]["pass_rate"], 4)),
    }
    notes = [
        f"種別ごとの p95 × 1.5 予算（トークン）: { {k: round(v) for k, v in budgets.items()} }",
        f"Goodhart 表: { {v: {k: round(x, 4) for k, x in row.items()} for v, row in table.items()} }",
        (
            f"v07 はステップ数を {steps_v07 / steps_v01:.4g} 倍に減らすが、同時に検証行動率が "
            f"{ver_v07 / ver_v01 if ver_v01 else 0.0:.4g} 倍に落ちる"
            f"（合格率は v01 {table['v01_baseline']['pass_rate']:.4g} に対し"
            f" v07 {table['v07_step_minimizer']['pass_rate']:.4g} で変わらない）。"
            "効率指標だけを見れば「改善」に見えるという原典 4.11 の指摘がそのまま出ている。"
            "しかも合格率が動かないので、成果物だけを見る評価ではこの劣化は捕まらない。"
        ),
        (
            "p95 予算は v01 自身から作っているので、v01 の超過率が 0 になるのは定義上ほぼ自明である。"
            "この基準が確かめているのは「予算が v04 のような冗長な版を弾くこと」の側だけである。"
        ),
    ]
    return finalize("E4-9", metrics, METHOD, notes, figures=[("Goodhart ペア", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
