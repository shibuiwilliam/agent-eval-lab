"""E8-2 鮮度と分布距離（原典 8.3）。"""

from __future__ import annotations

import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.core.registry import load_tasks
from agenteval.drift import distribution as dist_mod
from agenteval.drift.prodstream import ProdStream, paraphrase
from agenteval.reports import plotting

DAYS = [0, 1, 2, 3, 4]
N_SESSIONS = 60

METHOD = """`ProdStream`（ADR-006 の本番の代替）で 0〜4 日目のセッションを 60 件ずつ生成した。
日が進むと新カテゴリ（expense）の比率が上がり、プロンプトが「慣れた言い方」に寄る。
スイート側の分布はタスク YAML の `category` 分布。JS ダイバージェンスは原典 8.3 の式（底 2）。
警報の閾値は、スイート分布から同数を再サンプルしたときの JS の 95% 点（ブートストラップ、simulated）。
共適応の検出は、言い換え後のプロンプト群と元のプロンプト群の MMD（TF-IDF の文字 2〜3-gram ＋ RBF）で、
p 値は順列検定で出した。対照は同一分布からの再サンプル 2 群で、p ≥ 0.05 になる割合を見る。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    tasks = load_tasks()
    suite = dist_mod.suite_distribution([t.category for t in tasks.values()])
    stream = ProdStream(seed=seed)

    js_values = []
    new_ratios = []
    for day in DAYS:
        prod = stream.category_distribution(day, N_SESSIONS)
        js_values.append(dist_mod.js_divergence(suite, prod))
        new_ratios.append(prod.get("expense", 0.0))

    threshold = dist_mod.js_bootstrap_threshold(suite, N_SESSIONS, seed=seed)
    monotonic = int(all(b >= a - 1e-9 for a, b in pairwise(js_values)))
    alarm_day = next((i for i, r in enumerate(new_ratios) if r >= 0.25), None)
    new_category_alarm = int(alarm_day is not None and js_values[alarm_day] > threshold)

    sessions = stream.day_sessions(4, N_SESSIONS)
    originals = [s.original_prompt for s in sessions]
    paraphrased = [paraphrase(s.original_prompt) for s in sessions]
    mmd_result = dist_mod.mmd(originals, paraphrased, n_permutations=300, seed=seed)

    rng = np.random.default_rng(seed)
    control_p = []
    for _ in range(20):
        order = rng.permutation(len(originals))
        half = len(originals) // 2
        a = [originals[i] for i in order[:half]]
        b = [originals[i] for i in order[half:]]
        control_p.append(
            dist_mod.mmd(a, b, n_permutations=100, seed=int(rng.integers(0, 10**6))).p_value
        )
    control_rate = float(np.mean([p >= 0.05 for p in control_p]))

    fig = plotting.line_curve(
        "E8-2",
        "js_curve",
        "日数とスイート/本番のカテゴリ分布距離（JS）",
        "日",
        "JS ダイバージェンス",
        {
            "JS(suite, prod)": ([float(d) for d in DAYS], js_values),
            "帰無 95% 点": ([float(d) for d in DAYS], [threshold] * len(DAYS)),
        },
        "simulated (synthetic prod)",
    )

    metrics = {
        "js_monotonic": labeled(monotonic),
        "new_category_alarm": labeled(new_category_alarm),
        "mmd_p_value": labeled(mmd_result.p_value),
        "control_p_ge_005_rate": labeled(round(control_rate, 4)),
        "js_day0": labeled(js_values[0]),
        "js_day4": labeled(js_values[-1]),
        "js_threshold": labeled(round(threshold, 6)),
        "new_category_ratio_day4": labeled(round(new_ratios[-1], 4)),
        "mmd": labeled(mmd_result.mmd),
    }
    notes = [
        f"日ごとの JS: {js_values}、新カテゴリ比率: {[round(r, 3) for r in new_ratios]}",
        f"言い換えの MMD: {mmd_result.mmd}（p={mmd_result.p_value}）、対照の p 値: {control_p[:5]}...",
        (
            "本番は生成器で代替している（ADR-006）。分布シフトの大きさも言い換えの仕方も"
            "こちらで決めたものなので、この実験が示すのは「JS と MMD がシフトに反応すること」までで、"
            "実運用の分布シフトを捉えられることではない。"
        ),
        (
            "言い換えは決定的な置換規則（`PARAPHRASE_RULES`）で作っている。"
            "live では Haiku に生成させる経路を想定しているが未実行。"
        ),
    ]
    return finalize("E8-2", metrics, METHOD, notes, figures=[("JS の推移", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
