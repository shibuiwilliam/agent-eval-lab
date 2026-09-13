"""E4-8 pass@k / pass^k と一貫性（原典 4.10）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled, pass_map

from agenteval.process import consistency as consistency_mod
from agenteval.reports import plotting

TRIVIAL = ["T-901", "T-902"]
K = 3

METHOD = """コーパスの v01_baseline の反復（10 回）から、タスクごとに pass@k と pass^k を原典 4.10 の式で
計算した。境界タスクは v01 の合格率が 0.3〜0.7 のもの、自明タスクは T-901 / T-902。
ステップ数分散は v01 と v04_loopy の反復から出した。
pass@k は「k 回引いて 1 回でも成功する確率」、pass^k は「k 回引いて全部成功する確率」で、
差が大きいほど「動くこともある」テストである。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    rates = pass_map(runs, "v01_baseline")
    boundary = sorted(t for t, r in rates.items() if 0.3 <= r <= 0.7)

    def gaps(task_ids: list[str]) -> list[float]:
        out = []
        for task_id in task_ids:
            subset = [r for r in runs if r.version_id == "v01_baseline" and r.task_id == task_id]
            result = consistency_mod.evaluate(task_id, subset, ks=(1, 2, K))
            out.append(result.pass_at_k[K] - result.pass_pow_k[K])
        return out

    boundary_gaps = gaps(boundary)
    trivial_gaps = gaps(TRIVIAL)

    def step_variance(version: str) -> float:
        per_task = []
        for task_id in sorted(rates):
            subset = [r for r in runs if r.version_id == version and r.task_id == task_id]
            if len(subset) > 1:
                per_task.append(consistency_mod.evaluate(task_id, subset).step_variance)
        return float(np.mean(per_task)) if per_task else 0.0

    var_v01, var_v04 = step_variance("v01_baseline"), step_variance("v04_loopy")

    fig = plotting.bar_compare(
        "E4-8",
        "pass_gap",
        f"pass@{K} − pass^{K}（境界タスク平均 vs 自明タスク平均）",
        [f"境界タスク (n={len(boundary)})", f"自明タスク (n={len(TRIVIAL)})"],
        [float(np.mean(boundary_gaps)), float(np.mean(trivial_gaps))],
        f"pass@{K} − pass^{K}",
        "simulated",
        threshold=0.2,
    )

    metrics = {
        "boundary_gap": labeled(round(float(np.mean(boundary_gaps)), 4)),
        "trivial_gap": labeled(round(float(np.mean(trivial_gaps)), 4)),
        "var_steps_v04_gt_v01": labeled(int(var_v04 > var_v01)),
        "step_variance_v01": labeled(round(var_v01, 4)),
        "step_variance_v04": labeled(round(var_v04, 4)),
        "n_boundary_tasks": labeled(len(boundary)),
        "action_entropy_v01": labeled(
            round(
                float(
                    np.mean(
                        [
                            consistency_mod.action_entropy(
                                [
                                    r
                                    for r in runs
                                    if r.version_id == "v01_baseline" and r.task_id == t
                                ]
                            )
                            for t in boundary
                        ]
                    )
                ),
                4,
            )
        ),
    }
    notes = [
        f"境界タスク: {boundary}",
        (
            "自明タスク（T-901 / T-902）は全反復で合格するので pass@3 = pass^3 = 1.0 となり差は 0。"
            "境界タスクでは pass@3 が 1.0 近くまで上がる一方 pass^3 は低いままで、"
            "「3 回引けば 1 回は通る」と「3 回とも通る」の差が大きい。"
        ),
        (
            "sim モードでは同じ (task, version, repeat) は決定的だが、repeat をまたぐと"
            "「運」が変わるので反復間のばらつきが生じる。これは live のサンプリング揺れの代わりであり、"
            "live の揺れと同じ分布とは限らない。"
        ),
    ]
    return finalize(
        "E4-8", metrics, METHOD, notes, figures=[("pass@k と pass^k の差", fig)], seed=seed
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
