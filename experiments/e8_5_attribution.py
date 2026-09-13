"""E8-5 ドリフト帰属（要因別入替、原典 8.8）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, flake_band, labeled

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import Version, get_task, get_version
from agenteval.drift import attribution as attribution_mod
from agenteval.reports import plotting

N_TASKS = 10
REPEATS = 3
FACTOR_SOURCE = {
    "model": "v09_model_swap",
    "prompt": "v02_dateformat",
    "tool": "v06_toolschema_v2",
}

METHOD = """要因 {model, prompt, tool} の 2^3 = 8 組合せについて、各要因を植込み版から取った
合成版（model は v09 のモデル、prompt は v02 のプロンプト、tool は v06 のツール schema）を作り、
同じタスク 10 件 × 3 反復を実行して合格率を測った（原典 8.8）。
主効果はその要因を入れ替えたときの合格率変化の平均。予測原因は主効果の絶対値が最大の要因。
対照は無変更（全要因 off）で、主効果がフレーク帯の内側に収まることを確認する。
2 要因同時変更は prompt + tool の組で、上位 2 要因が一致するかを見る。"""


def make_version(combo: dict[str, bool], active: set[str]) -> Version:
    """要因の組合せから合成版を作る。

    `active` に入っていない要因は「その配備では変わっていない」ので、ON でも OFF と同じにする。
    要因別入替（8.8）は「旧配備と新配備で実際に差がある要因」を入れ替えるものなので、
    変わっていない要因を入れ替えてしまうと、起きていない変化に主効果が付いてしまう。
    """
    base = get_version("v01_baseline")
    data = base.model_dump()
    data["id"] = (
        "cell__"
        + "_".join(f"{k}{int(v)}" for k, v in combo.items())
        + "__"
        + "-".join(sorted(active))
    )
    if combo["model"] and "model" in active:
        data["model"] = get_version(FACTOR_SOURCE["model"]).model
    if combo["prompt"] and "prompt" in active:
        data["system_prompt"] = get_version(FACTOR_SOURCE["prompt"]).system_prompt
    if combo["tool"] and "tool" in active:
        data["toolset"] = get_version(FACTOR_SOURCE["tool"]).toolset
    return Version.model_validate(data)


def cell_pass_rate(version: Version, task_ids: list[str], seed: int) -> float:
    passed = 0
    total = 0
    for task_id in task_ids:
        for repeat in range(REPEATS):
            run = run_task(
                get_task(task_id),
                version,
                RunOptions(
                    mode="sim",
                    seed=seed,
                    repeat=repeat,
                    keep_snapshots=False,
                    run_id_override=f"attr__{version.id}__{task_id}__r{repeat}",
                ),
            )
            passed += int(run.passed())
            total += 1
    return passed / total if total else 0.0


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")
    band = sum(bands.values()) / len(bands)

    # 対象は scheduling タスク（3 要因すべてが効きうる）
    task_ids = sorted({r.task_id for r in runs if r.task_id.startswith("T-0")})[:N_TASKS]

    def factorial(active: set[str]) -> dict[str, float]:
        """指定の要因だけが実際に入れ替わる 2^3 の合格率表を作る。"""
        out: dict[str, float] = {}
        for combo in attribution_mod.combinations():
            version = make_version(combo, active)
            out[attribution_mod.cell_key(combo)] = cell_pass_rate(version, task_ids, seed)
        return out

    # 単一要因の植込み 3 種: その要因だけが入れ替わっている配備
    single_match = 0
    single_rows = []
    for factor in attribution_mod.FACTORS:
        result_single = attribution_mod.main_effects(factorial({factor}))
        single_rows.append(
            {
                "planted": factor,
                "predicted": result_single.predicted_cause,
                "effects": result_single.main_effects,
            }
        )
        single_match += int(result_single.predicted_cause == factor)

    # 2 要因同時変更: prompt と tool だけが入れ替わっている配備（model は変わっていない）
    two_factor = {"prompt", "tool"}
    two_result = attribution_mod.main_effects(factorial(two_factor))
    top2_match = int(set(two_result.top(2)) == two_factor)

    # 参考: 3 要因すべてが入れ替わった場合の主効果
    rates = factorial(set(attribution_mod.FACTORS))
    result = attribution_mod.main_effects(rates)

    # 対照（無変更）: 同じ版を別の seed で 2 回走らせ、その差がフレーク帯の内側に収まるか
    baseline_a = cell_pass_rate(get_version("v01_baseline"), task_ids, seed)
    baseline_b = cell_pass_rate(get_version("v01_baseline"), task_ids, seed + 991)
    control_effects = attribution_mod.AttributionResult(
        main_effects=dict.fromkeys(attribution_mod.FACTORS, round(baseline_b - baseline_a, 4)),
        predicted_cause="none",
    )
    control_alarm = attribution_mod.alarm(control_effects, band)

    fig = plotting.bar_compare(
        "E8-5",
        "main_effects",
        "要因ごとの主効果（合格率の変化）",
        list(result.main_effects),
        list(result.main_effects.values()),
        "主効果",
        "simulated",
    )

    metrics = {
        "single_factor_match_rate": labeled(round(single_match / len(attribution_mod.FACTORS), 4)),
        "two_factor_top2_match": labeled(top2_match),
        "control_alarm": labeled(int(control_alarm["alarm"])),
        "effect_model": labeled(result.main_effects["model"]),
        "effect_prompt": labeled(result.main_effects["prompt"]),
        "effect_tool": labeled(result.main_effects["tool"]),
        "predicted_cause": labeled(result.predicted_cause),
        "two_factor_top2": labeled(",".join(two_result.top(2))),
        "effect_prompt_two_factor": labeled(two_result.main_effects["prompt"]),
        "effect_tool_two_factor": labeled(two_result.main_effects["tool"]),
        "effect_model_two_factor": labeled(two_result.main_effects["model"]),
        "flake_band": labeled(round(band, 4)),
        "n_cells": labeled(len(rates)),
        "control_delta": labeled(round(baseline_b - baseline_a, 4)),
    }
    notes = [
        f"3 要因すべてを入れ替えた場合のセル別合格率: { {k: round(v, 3) for k, v in rates.items()} }",
        f"2 要因（prompt + tool）の主効果: { {k: round(v, 4) for k, v in two_result.main_effects.items()} }",
        f"単一要因の判定: {single_rows}",
        (
            f"対照（無変更）: 同じ v01 を seed を変えて 2 回走らせた合格率の差は "
            f"{round(baseline_b - baseline_a, 4)} で、フレーク帯 {round(band, 4)} の内側なら警報なしと判定する。"
        ),
        (
            "2 要因のケースでは model 要因を入れ替えない（その配備では model が変わっていないため）。"
            "最初の実装では 3 要因すべてを入れ替えた表から主効果を出していたので、"
            "起きていないモデル変更に主効果が付き、上位 2 要因が {prompt, model} になってしまった。"
            "要因別入替は「旧配備と新配備で実際に差がある要因」だけを入れ替える必要がある。"
        ),
        (
            "合成版は v01 のプロンプト・モデル・ツールを差し替えて作っている。"
            "prompt 要因には v02 のプロンプト全体が入るので、date_format 以外の section も一緒に入れ替わる。"
            "要因の粒度は「版の属性」であって section 単位ではない。"
        ),
    ]
    return finalize("E8-5", metrics, METHOD, notes, figures=[("主効果", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
