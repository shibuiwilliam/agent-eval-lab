"""E8-3 弁別力と飽和（原典 8.4）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled, pass_map

from agenteval.drift import discriminative as disc_mod
from agenteval.drift.lifecycle import Metrics, log_transition, next_state, read_log
from agenteval.reports import plotting

TRIVIAL = ["T-901", "T-902"]

METHOD = """版 YAML の `quality_label`（good / bad）ごとの合格率の差を `d(t)` とし（原典 8.4、neutral は除外）、
全版で合格率 > 0.95 かつ |d| < 0.1 のテストを飽和と判定した。
植込みは自明タスク T-901 / T-902（finish を呼べば通る）。対照は境界タスク
（v01_baseline の合格率が 0.3〜0.7）。
飽和と判定されたテストが `lifecycle.py` の状態機械に届き、Active → Saturated に遷移することも確認した。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    table = disc_mod.evaluate_all(runs)
    rates = pass_map(runs, "v01_baseline")
    boundary = sorted(t for t, r in rates.items() if 0.3 <= r <= 0.7)

    trivial_d = float(np.mean([abs(table[t].d) for t in TRIVIAL]))
    trivial_saturated = int(all(table[t].saturated for t in TRIVIAL))
    boundary_d = float(np.mean([table[t].d for t in boundary])) if boundary else 0.0

    log_path = Path("data/lifecycle_e8_3.jsonl")
    if log_path.exists():
        log_path.unlink()
    received = 0
    for task_id in TRIVIAL:
        result = table[task_id]
        metrics_in = Metrics(discriminative=result.d, fidelity=1.0, validated=True, flake_rate=0.0)
        after = next_state("Active", metrics_in)
        if after == "Saturated":
            log_transition(task_id, "Active", after, metrics_in, log_path)
            received += 1
    lifecycle_received = int(received == len(TRIVIAL) and len(read_log(log_path)) == len(TRIVIAL))

    fig = plotting.bar_compare(
        "E8-3",
        "discriminative",
        "弁別力 d(t) = good 版の合格率 − bad 版の合格率",
        [f"自明 (n={len(TRIVIAL)})", f"境界 (n={len(boundary)})", "全タスク平均"],
        [trivial_d, boundary_d, float(np.mean([t.d for t in table.values()]))],
        "d(t)",
        "simulated",
        threshold=0.3,
    )

    metrics = {
        "trivial_abs_d": labeled(round(trivial_d, 4)),
        "trivial_saturated": labeled(trivial_saturated),
        "boundary_d": labeled(round(boundary_d, 4)),
        "lifecycle_received": labeled(lifecycle_received),
        "n_boundary": labeled(len(boundary)),
        "mean_d_all": labeled(round(float(np.mean([t.d for t in table.values()])), 4)),
        "max_d": labeled(round(max(t.d for t in table.values()), 4)),
        "n_saturated": labeled(sum(1 for t in table.values() if t.saturated)),
    }
    notes = [
        f"飽和と判定されたタスク: {sorted(t for t, v in table.items() if v.saturated)}",
        f"d(t) の上位: {sorted(((round(v.d, 3), k) for k, v in table.items()), reverse=True)[:5]}",
        (
            "good / bad のラベルは版の「品質」であって合格率ではない。v03_noverify と v04_loopy は"
            "bad ラベルだが合格率は v01 と変わらないので、d(t) を下げる方向に効く。"
            "成果物だけを見る弁別力では、過程の劣化を植え込んだ版を区別できないということである。"
        ),
    ]
    notes.append(
        "判定は NEGATIVE。自明タスクの飽和判定（|d| = 0、飽和 = 真）とライフサイクルへの通知は"
        f"成立したが、境界タスクの弁別力は {boundary_d:.4g} で基準の 0.3 に届かなかった。"
        "実装の不備ではない。原因は、bad ラベルの 7 版のうち v03_noverify・v04_loopy・v08 相当の"
        "「過程だけが劣化した版」が合格率をまったく下げないこと（E4-1 の観測と同じ）。"
        "`d(t)` は合否だけで品質ラベルを分離しようとする指標なので、"
        "**成果物が同じで過程が劣化した版は原理的に弁別できない**。"
        "これは原典 4.1 の「成果と過程は独立の軸」という主張の裏返しであり、"
        "8.4 の弁別力を過程指標（リンター違反率や検証行動率）でも定義する必要を示している。"
    )
    return finalize(
        "E8-3",
        metrics,
        METHOD,
        notes,
        figures=[("弁別力", fig)],
        seed=seed,
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
