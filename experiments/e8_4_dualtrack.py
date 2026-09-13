"""E8-4 二重トラック κ（原典 8.6）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version, load_tasks
from agenteval.drift import dualtrack as dualtrack_mod
from agenteval.env.injectors import DriftInjector
from agenteval.reports import plotting

REPEATS = 5

METHOD = """`external_lookup` を必ず使うタスク（mixed カテゴリ）に限定し、同じタスク集合を
「模擬トラック」（外部サービス v1）と「ライブトラック」（現在の外部サービス）で実行して、
`outcome.passed` の一致を Cohen κ で測った（原典 8.6）。
注入前は両トラックとも v1、注入後はライブトラックだけ `DriftInjector.bump_external(v2)` を適用する。
κ は `sklearn.metrics.cohen_kappa_score`。両群の合否に分散が無い場合は κ が未定義になるため、
完全一致なら 1.0、完全不一致なら 0.0 として扱う（実装の注記）。"""


def run_track(
    task_ids: list[str], version_id: str, external: str, drift: bool, seed: int
) -> list[Any]:
    version = get_version(version_id)
    runs = []
    for task_id in task_ids:
        for repeat in range(REPEATS):
            injector = DriftInjector(external_version="v2") if drift else None
            runs.append(
                run_task(
                    get_task(task_id),
                    version,
                    RunOptions(
                        mode="sim",
                        seed=seed,
                        repeat=repeat,
                        external_version=external,
                        drift=injector,
                        keep_snapshots=False,
                        run_id_override=f"dt__{version_id}__{task_id}__{external}__{int(drift)}__r{repeat}",
                    ),
                )
            )
    return runs


def kappa_over_repeats(mock: list[Any], live_runs: list[Any]) -> float:
    """反復ごとに κ を出して平均する（1 反復 = 1 タスク集合の実行）。"""
    import numpy as np

    values = []
    for repeat in range(REPEATS):
        a = [r for r in mock if r.repeat == repeat]
        b = [r for r in live_runs if r.repeat == repeat]
        values.append(dualtrack_mod.kappa(a, b).kappa)
    return float(np.mean(values))


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    tasks = load_tasks()
    task_ids = sorted(t for t, v in tasks.items() if v.sim.action == "lookup_cost")

    # 2 つのトラックは「外部サービスの版」だけが違う（seed は同じ）。
    # seed を変えると版以外の揺れが混ざり、κ がドリフト以外の理由で下がる。
    mock = run_track(task_ids, "v01_baseline", "v1", drift=False, seed=seed)
    live_before = run_track(task_ids, "v01_baseline", "v1", drift=False, seed=seed)
    live_after = run_track(task_ids, "v01_baseline", "v1", drift=True, seed=seed)

    before = kappa_over_repeats(mock, live_before)
    after = kappa_over_repeats(mock, live_after)

    fig = plotting.bar_compare(
        "E8-4",
        "kappa",
        "模擬トラックとライブトラックの合否一致 κ",
        ["注入前", "注入後（external v2）"],
        [before, after],
        "Cohen κ",
        "simulated",
        threshold=0.6,
    )

    metrics = {
        "kappa_before": labeled(round(before, 4)),
        "kappa_drop": labeled(round(before - after, 4)),
        "kappa_after": labeled(round(after, 4)),
        "n_tasks": labeled(len(task_ids)),
        "n_runs": labeled(len(mock) + len(live_before) + len(live_after)),
        "mock_pass_rate": labeled(round(sum(r.passed() for r in mock) / len(mock), 4)),
        "live_after_pass_rate": labeled(
            round(sum(r.passed() for r in live_after) / len(live_after), 4)
        ),
    }
    notes = [
        f"対象タスク（external_lookup を使うもの）: {task_ids}",
        (
            "**注入前の κ = 1.0 は sim の構造から自明である**。sim モードでは同じ (task, seed, repeat) の"
            "実行が決定的なので、外部サービスの版が同じなら模擬とライブは必ず一致する。"
            "live では同じ条件でも応答が揺れるため κ < 1.0 になる。"
            "したがってこの実験が示したのは「ドリフト注入で κ が下がること」だけで、"
            "「注入前の κ が高いこと」は検証したことにならない。"
        ),
        (
            "κ が下がる仕組みは、外部サービスの応答キーが `price` → `unit_price` に変わり、"
            "エージェントが価格を読み取れずに不合格になること。模擬側は v1 のままなので合格する。"
        ),
    ]
    return finalize("E8-4", metrics, METHOD, notes, figures=[("κ の変化", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
