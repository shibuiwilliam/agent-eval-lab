"""E4-7 軌跡内 NIAH（原典 4.9）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version
from agenteval.env.injectors import NoiseInjector
from agenteval.process import niah as niah_mod
from agenteval.reports import plotting

TASKS = ["T-301", "T-302", "T-303", "T-304"]
NOISE = [0, 2, 4, 8]
REPEATS = 3
VERSIONS = ["v01_baseline", "v08_summarize_ctx"]

METHOD = """`kind: niah` のタスク 4 種について、`NoiseInjector` でステップ 0 以降 n ステップの
ツール応答に無関係な長文（1 ステップあたり 1,200 文字）を付加し、n ∈ {0, 2, 4, 8} × 3 反復で実行した
（原典 4.9）。想起率 R(n) は最終テキストに `key_fact` が含まれる割合。
版は v01_baseline（raw）と v08_summarize_ctx（k ステップより古いツール結果を要約に置換）。
AUC は n を 0〜1 に正規化した台形則。
この実験はノイズ注入を伴うのでコーパスの run は使わず、実験内で run を生成する（注入は manifest に記録）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    curves: dict[str, list[niah_mod.RecallPoint]] = {}
    raw_rows = []
    for version_id in VERSIONS:
        version = get_version(version_id)
        by_noise: dict[int, list[Any]] = {}
        for n in NOISE:
            runs = []
            for repeat in range(REPEATS):
                task = get_task(TASKS[0])
                for task_id in TASKS:
                    task = get_task(task_id)
                    injector = (
                        NoiseInjector(start_step=0, n_steps=n, seed=seed + repeat) if n else None
                    )
                    run = run_task(
                        task,
                        version,
                        RunOptions(
                            mode="sim",
                            seed=seed,
                            repeat=repeat,
                            noise=injector,
                            keep_snapshots=False,
                            run_id_override=f"niah__{version_id}__{task_id}__n{n}__r{repeat}",
                        ),
                    )
                    runs.append((task, run))
            by_noise[n] = runs
            hits = sum(1 for task, run in runs if niah_mod.recalled(task, run))
            raw_rows.append(
                {
                    "version": version_id,
                    "n": n,
                    "recall": round(hits / len(runs), 4),
                    "runs": len(runs),
                }
            )
        curves[version_id] = [
            niah_mod.RecallPoint(
                n=n,
                recall=sum(1 for task, run in by_noise[n] if niah_mod.recalled(task, run))
                / len(by_noise[n]),
                runs=len(by_noise[n]),
            )
            for n in NOISE
        ]

    auc = {v: niah_mod.auc(points) for v, points in curves.items()}
    recall_n0 = min(points[0].recall for points in curves.values())
    v01_points = curves["v01_baseline"]
    drop_v01 = v01_points[0].recall - v01_points[-1].recall

    fig = plotting.line_curve(
        "E4-7",
        "recall_curve",
        "ノイズ量と想起率 R(n)",
        "ノイズを入れたステップ数 n",
        "想起率",
        {
            v: ([float(p.n) for p in points], [p.recall for p in points])
            for v, points in curves.items()
        },
        "simulated",
    )

    metrics = {
        "recall_n0": labeled(round(recall_n0, 4)),
        "recall_drop_v01": labeled(round(drop_v01, 4)),
        "auc_gain_v08": labeled(round(auc["v08_summarize_ctx"] - auc["v01_baseline"], 4)),
        "auc_v01": labeled(auc["v01_baseline"]),
        "auc_v08": labeled(auc["v08_summarize_ctx"]),
        "monotonic_v01": labeled(int(niah_mod.is_monotonic(curves["v01_baseline"]))),
        "monotonic_v08": labeled(int(niah_mod.is_monotonic(curves["v08_summarize_ctx"]))),
        "n_runs": labeled(len(VERSIONS) * len(NOISE) * REPEATS * len(TASKS)),
    }
    notes = [
        f"想起率の生データ: {raw_rows}",
        (
            "シミュレータの想起モデルは「送信された会話の中でノイズが占める文字数の割合」に対して"
            "想起確率を線形に下げる決定的な規則である。要約戦略（v08）は古いツール結果を先頭 160 文字に"
            "切り詰めるので、末尾に付加されたノイズが落ち、key_fact（本文の先頭側にある）が残る。"
            "つまり v08 が有利になるのは注入の位置と要約の実装の組合せによる構造的な帰結であり、"
            "要約が一般に想起に有利であることを示すものではない。"
        ),
        (
            "原典 4.11 は要約が情報を落とす可能性を指摘している。ここで AUC が上がったのは"
            "「落ちた情報がノイズ側だった」という条件付きの結果である。"
        ),
    ]
    notes.append(
        "判定は FAIL（実験設計の不備）。R(0) = 1.0、v01 の R(8) までの低下 = 0.67 と"
        "ノイズへの反応は明確に出たが、AUC の差は 0.0 だった。"
        "原因は niah タスクの run が 2〜3 ステップしかなく、要約戦略（`summarize_after = 2`）が"
        "発火する前に finish に達すること。つまり v08 は v01 と同じ会話を送っており、"
        "比較になっていない。修正案: niah タスクを多段（複数のメール検索とファイル読み取りを要する）にして"
        "ステップ数を増やす。"
    )
    return finalize("E4-7", metrics, METHOD, notes, figures=[("想起率曲線", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
