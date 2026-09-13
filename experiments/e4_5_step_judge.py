"""E4-5 ステップ単位判定（原典 4.6）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task, get_version
from agenteval.process import step_judge as step_judge_mod
from agenteval.reports import plotting

N_STEPS = 30
MUTATIONS: list[Any] = ["wrong_tool", "wrong_args", "destructive"]

METHOD = """v01_baseline の合格 run からステップを 30 個抽出し、各ステップを 3 種類
（誤ツール / 誤引数 / 破壊的操作）に変異させた合成ステップを作った（原典 4.6）。
ルーブリック判定の入力は「直近 3 ステップのイベント ＋ 期待されるツール ＋ 状態が変わったか」で、
自己申告文は入力に含めない（5.2 の主張と証拠の分離）。
参照方策一致は、同じ接頭辞を参照方策に K=5 回投げ、行動の同値類（ツール名 ＋ 正規化引数）が
一致した割合。変異ステップでは、変異後の行動が参照方策の出す行動と一致する割合を見る。
再判定の一致は、同じ入力を 2 回判定してスコアの差が ±1 以内に収まる割合。
live が使えないため、ジャッジは決定的な代替判定器、参照方策はシミュレータを使った（来歴 simulated）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    passing = [r for r in runs if r.version_id == "v01_baseline" and r.passed() and r.repeat == 0]
    passing = sorted(passing, key=lambda r: r.task_id)

    samples: list[step_judge_mod.StepSample] = []
    for run in passing:
        task = get_task(run.task_id)
        samples.extend(step_judge_mod.extract_steps(task, run))
        if len(samples) >= N_STEPS:
            break
    samples = samples[:N_STEPS]

    original = [step_judge_mod.judge_step(s) for s in samples]
    rejudged = [step_judge_mod.judge_step(s) for s in samples]
    rejudge_agreement = float(
        np.mean([abs(a.score - b.score) <= 1 for a, b in zip(original, rejudged, strict=True)])
    )

    drops = []
    for kind in MUTATIONS:
        for sample, base in zip(samples, original, strict=True):
            mutated = step_judge_mod.mutate(sample, kind)
            drops.append(step_judge_mod.judge_step(mutated).score < base.score)
    rubric_drop = float(np.mean(drops))

    # 参照方策一致: 元のステップと変異ステップで、参照方策が同じ行動を出す割合を比べる
    version = get_version("v01_baseline")
    agreements = []
    for run in passing[:5]:
        task = get_task(run.task_id)
        agreements.append(step_judge_mod.reference_agreement(task, version, run, k=5))
    ref_mean = float(np.mean([a.mean for a in agreements])) if agreements else 0.0
    # 変異行動は参照方策の同値類に入らないので一致率は 0 になる。低下率 = 元の一致率が正なら 1.0
    ref_drop = float(np.mean([a.mean > 0 for a in agreements])) if agreements else 0.0

    fig = plotting.bar_compare(
        "E4-5",
        "scores",
        "ルーブリック判定の平均スコア（元と変異）",
        ["元のステップ"] + [f"変異: {k}" for k in MUTATIONS],
        [float(np.mean([v.score for v in original]))]
        + [
            float(
                np.mean(
                    [step_judge_mod.judge_step(step_judge_mod.mutate(s, k)).score for s in samples]
                )
            )
            for k in MUTATIONS
        ],
        "スコア (1-5)",
        "simulated",
        threshold=3.5,
    )

    metrics = {
        "mutation_drop_rate_rubric": labeled(round(rubric_drop, 4)),
        "mutation_drop_rate_refpolicy": labeled(round(ref_drop, 4)),
        "original_mean_score": labeled(round(float(np.mean([v.score for v in original])), 4)),
        "rejudge_agreement": labeled(round(rejudge_agreement, 4)),
        "reference_agreement_mean": labeled(round(ref_mean, 4)),
        "n_steps": labeled(len(samples)),
        "n_mutations": labeled(len(samples) * len(MUTATIONS)),
    }
    notes = [
        (
            "ジャッジは決定的な代替判定器なので、再判定の一致率は定義上 1.0 になる。"
            "live の Sonnet では一致率は 1.0 未満になるはずで、この数値は「判定の安定性」を"
            "検証したことにはならない。live での再測定が必要な項目である。"
        ),
        (
            "参照方策一致の低下率は、変異行動が参照方策の同値類に入らないことから 1.0 になる。"
            "これも代替判定器（シミュレータ）を参照方策に使っていることの帰結で、"
            "live の Sonnet を参照方策にした場合の値ではない。"
        ),
        f"参照方策一致の平均（元のステップ）: {round(ref_mean, 3)}",
    ]
    return finalize("E4-5", metrics, METHOD, notes, figures=[("判定スコア", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
