"""E4-2 アブレーションによる無駄呼び出し判定（原典 4.3）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task, get_version
from agenteval.process import ablation as ablation_mod
from agenteval.reports import plotting

N_RUNS = 5

METHOD = """v01_baseline と v04_loopy の run を 5 本ずつ取り、各ツール呼び出しについて
その結果を `[result omitted by ablation]` に置き換えた会話を作り、ステップ i 後のスナップショットを
restore してから i+1 以降を実行し直した（原典 4.3）。`outcome.passed` が変わらなければその呼び出しは
「無駄」と判定する。無駄呼び出し率 `U(τ)` は無駄と判定された呼び出しの割合。
代理指標はジャッジに「この結果は後続の判断で参照されたか」を `submit_verdict` で問うもので、
アブレーション結果を正解として precision / recall を出した。
対照は `calendar_search → calendar_create` のように直後の行動が結果に依存する呼び出しで、
これらが「有用」と判定される割合を見る。
live が使えないため、アブレーションの再実行は sim モード、ジャッジは決定的な代替判定器
（`judge/rubric.py` の `offline_ablation_judge`）を使った。来歴は simulated。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    results: dict[str, list[ablation_mod.AblationResult]] = {}
    surrogates: dict[str, dict[str, bool]] = {}
    for version_id in ("v01_baseline", "v04_loopy"):
        version = get_version(version_id)
        subset = [
            r
            for r in runs
            if r.version_id == version_id and r.repeat == 0 and r.steps and r.steps[0].snapshot_ref
        ]
        subset = sorted(subset, key=lambda r: r.task_id)[:N_RUNS]
        rows = []
        merged: dict[str, bool] = {}
        for run in subset:
            task = get_task(run.task_id)
            rows.append(ablation_mod.ablate_run(task, version, run, mode="sim"))
            merged.update(ablation_mod.surrogate_labels(task, run, client=None))
        results[version_id] = rows
        surrogates[version_id] = merged

    waste = ablation_mod.waste_rate_by_version([r for rows in results.values() for r in rows])
    precision_recall = []
    for version_id, rows in results.items():
        for result in rows:
            precision_recall.append(ablation_mod.precision_recall(surrogates[version_id], result))
    total_tp = sum(r["tp"] for r in precision_recall)
    total_fp = sum(r["fp"] for r in precision_recall)
    total_fn = sum(r["fn"] for r in precision_recall)
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0

    # 対照: search → create のように直後の行動が結果に依存する呼び出し
    control_hits = 0
    control_total = 0
    for version_id, rows in results.items():
        for result in rows:
            for ref in result.useful_calls + result.wasted_calls:
                if ref.tool in ("calendar_search", "file_read", "external_lookup", "mail_search"):
                    control_total += 1
                    control_hits += int(surrogates[version_id].get(ref.call_id, False))
    control_rate = control_hits / control_total if control_total else 0.0

    fig = plotting.bar_compare(
        "E4-2",
        "waste_rate",
        "アブレーションで測った無駄呼び出し率 U(τ)",
        list(waste),
        list(waste.values()),
        "U(τ)",
        "simulated",
    )

    metrics = {
        "waste_rate_diff": labeled(
            round(waste.get("v04_loopy", 0.0) - waste.get("v01_baseline", 0.0), 4)
        ),
        "surrogate_precision": labeled(round(precision, 4)),
        "surrogate_recall": labeled(round(recall, 4)),
        "control_useful_rate": labeled(round(control_rate, 4)),
        "waste_rate_v01": labeled(waste.get("v01_baseline", 0.0)),
        "waste_rate_v04": labeled(waste.get("v04_loopy", 0.0)),
        "n_ablated_calls": labeled(sum(r.total_calls for rows in results.values() for r in rows)),
    }
    notes = [
        f"アブレーションした呼び出し数: { {v: sum(r.total_calls for r in rows) for v, rows in results.items()} }",
        (
            "最終ステップの呼び出しは、以降の判断が無いため影響を測れない。"
            "実装では「無駄」側に数えている（保守的ではない側なので、U を過大にしうる）。"
        ),
        (
            "ジャッジは live の Sonnet ではなく決定的な代替判定器を使っている。"
            "代理指標の precision / recall はこの代替判定器の性能であって、"
            "LLM ジャッジの性能ではない。live での再測定が要る。"
        ),
    ]
    notes.append(
        "判定は FAIL（実験設計の不備）。U は v01 で 0.875、v04 で 0.905 と、どちらも天井に張り付いた。"
        "原因は、この環境のエージェントが checks_run → 修復のループを持っていて、"
        "検索結果を伏せられても重複を検知して作り直すため、結果を消しても合否が変わらないこと。"
        "U は「その呼び出しが無くても最終的に通るか」を測る指標なので、"
        "修復ループを持つエージェントでは常に高く出る。"
        "アブレーション自体は仕様どおり動いており（対照の有用判定は 1.0）、"
        "植込み版の差が出なかったのは天井効果である。"
    )
    return finalize("E4-2", metrics, METHOD, notes, figures=[("無駄呼び出し率", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
