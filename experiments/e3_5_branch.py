"""E3-5 接頭辞キャッシュと分岐再実行（原典 3.6）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task, get_version
from agenteval.pts.prefix_cache import BranchRunner, validate_branching
from agenteval.reports import plotting

N_TASKS = 20

METHOD = """v01_baseline の run（repeat=0。スナップショットを保存してある）を base として、
新版（v02_dateformat / v09_model_swap / v01_baseline 自身）で接頭辞を辿り直した。
各ステップで新版に旧軌跡の会話を渡し、返った行動を `normalize_args` で旧行動と比較する。
一致すれば次へ、不一致なら `divergence_step` を記録し、そのステップのスナップショットを restore して
通常ループで最後まで走る（原典 3.6）。
課金されるステップ数 `live_steps` は、判断確認のうちリクエストが旧版と完全に一致しないもの
（＝版ハッシュが違う場合の確認呼び出し）＋ 分岐後の実行ステップ数とした。版ハッシュが同じなら
カセットに当たるので 0 と数える。妥当性検査は同じ (task, version) の通常実行と `outcome.passed` を比較した。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    base_runs = [
        r
        for r in runs
        if r.version_id == "v01_baseline" and r.repeat == 0 and r.steps and r.steps[0].snapshot_ref
    ]
    base_runs = sorted(base_runs, key=lambda r: r.task_id)[:N_TASKS]
    normal = {(r.task_id, r.version_id): r for r in runs if r.repeat == 0}

    results: dict[str, list[Any]] = {}
    pairs: list[tuple[Any, Any, Any, Any]] = []
    for version_id in ("v02_dateformat", "v09_model_swap", "v01_baseline"):
        version = get_version(version_id)
        rows = []
        for base in base_runs:
            task = get_task(base.task_id)
            runner = BranchRunner(mode="sim", seed=base.seed)
            result = runner.run(task, version, base)
            rows.append(result)
            reference = normal.get((base.task_id, version_id))
            if reference is not None:
                pairs.append((task, version, result.run, reference))
        results[version_id] = rows

    validation = validate_branching(pairs)
    divergences = {
        v: [r.divergence_step for r in rows if r.divergence_step is not None]
        for v, rows in results.items()
    }
    median_v02 = (
        float(np.median(divergences["v02_dateformat"])) if divergences["v02_dateformat"] else 99.0
    )
    median_v09 = (
        float(np.median(divergences["v09_model_swap"])) if divergences["v09_model_swap"] else 99.0
    )
    ratio_v02 = float(np.mean([r.live_step_ratio for r in results["v02_dateformat"]]))
    self_live = sum(r.live_steps for r in results["v01_baseline"])

    # トークンベースの節約率も併記する（.claude/rules/pts.md）。
    # 判断確認の呼び出しは接頭辞が同じなのでプロンプトキャッシュが効き、ステップ数ほどは高くつかない。
    base_tokens = {r.task_id: max(1, r.cost.total_tokens) for r in base_runs}
    token_ratio = float(
        np.mean(
            [
                r.run.cost.total_tokens / base_tokens[r.run.task_id]
                for r in results["v02_dateformat"]
                if r.run.task_id in base_tokens
            ]
        )
    )

    fig = plotting.bar_compare(
        "E3-5",
        "live_step_ratio",
        "分岐再実行で課金されたステップの割合（通常実行比）",
        ["v02（小変更）", "v09（モデル入替）", "v01（自己分岐）"],
        [
            ratio_v02,
            float(np.mean([r.live_step_ratio for r in results["v09_model_swap"]])),
            float(np.mean([r.live_step_ratio for r in results["v01_baseline"]])),
        ],
        "live ステップ比",
        "simulated",
        threshold=0.6,
    )

    metrics = {
        "outcome_agreement": labeled(validation["agreement"]),
        "live_step_ratio_v02": labeled(round(ratio_v02, 4)),
        "divergence_order_ok": labeled(int(median_v09 < median_v02)),
        "self_branch_live_steps": labeled(self_live),
        "median_divergence_v02": labeled(median_v02),
        "median_divergence_v09": labeled(median_v09),
        "n_pairs": labeled(validation["n"]),
        "token_ratio_v02": labeled(round(token_ratio, 4)),
    }
    notes = [
        f"分岐点の分布: v02={divergences['v02_dateformat']}, v09={divergences['v09_model_swap']}",
        f"自己分岐（v01→v01）の分岐件数: {len(divergences['v01_baseline'])}（0 が期待値）",
        (
            "sim モードでは同じ版に対する応答が決定的なので、自己分岐は必ず一致する。"
            "live では同じ版でも応答が揺れるため、自己分岐の一致率は 1.0 未満になりうる。"
            "この点は sim の限界であり、live での再確認が要る。"
        ),
        (
            "モデル入替（v09）の分岐点の中央値が v02 より小さいという仮説は、"
            "分岐が起きなかった場合に中央値が定義できないため、件数と併せて読む必要がある。"
        ),
    ]
    notes.append(
        "**ADR-021 による訂正**: 改訂前は分岐したステップの判断確認を課金に数えたうえで、"
        "その直後に同じステップをもう一度モデルに投げていた（`resume_from=divergence`）。"
        "実装としては分岐確認で得た応答をそのまま再開に使えるので、1 分岐につき 1 ステップぶん"
        "過大に数えていた。これを直して v02 の live ステップ比は 1.113 → 1.000 になった。"
        "**判定は NEGATIVE のまま**である（基準は 0.6）。"
    )
    notes.append(
        "判定は NEGATIVE。手法の正しさに関わる部分（分岐再実行と通常実行の outcome 一致率 = 1.0、"
        "自己分岐の課金ステップ = 0）は成立したが、節約の主張が成立しなかった。"
        "原因は、接頭辞を辿り直す「判断確認」の呼び出しが 1 ステップにつき 1 回必要で、"
        "版が変われば必ず課金されること。v02 は 2 ステップ目（calendar_create）で書式が変わるため"
        "分岐が早く、確認 2 回 ＋ 分岐後の再実行でかえって通常実行より高くつく。"
        f"トークン比は {round(token_ratio, 3)} で、ステップ比より低い（接頭辞が同じぶんキャッシュが効く）。"
    )
    return finalize(
        "E3-5",
        metrics,
        METHOD,
        notes,
        figures=[("live ステップ比", fig)],
        seed=seed,
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
