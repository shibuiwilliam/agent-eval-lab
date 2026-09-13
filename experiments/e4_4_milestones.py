"""E4-4 半順序マイルストーンの部分点（原典 4.5）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task
from agenteval.core.schema import Run, Step, ToolCall
from agenteval.process.milestones import evaluate as evaluate_milestones
from agenteval.reports import plotting

VERSIONS = ["v01_baseline", "v03_noverify", "v05_unsafe_delete", "v07_step_minimizer"]

METHOD = """コーパスの各 run について、実行時に記録した `milestone_score`（ステップごとの状態列に対する
半順序判定）を版ごとに平均し、同じ版の acceptance 合格率との Spearman 順位相関を取った（原典 4.5）。
合成軌跡では、(a) 制約どおりの順序（検索 → 作成 → 検査）、(b) 順序違い（検査 → 作成）、
(c) 到達しない軌跡 の 3 本を手で組み立て、部分点が期待どおりになるかを確認した。
`after` の制約に反して到達したマイルストーンは部分点に数えない実装である。"""


def synthetic_check() -> dict[str, Any]:
    """順序違いの合成軌跡が減点され、正しい順序が満点になることを確認する。"""
    task = get_task("T-001")

    def build(tools_per_step: list[list[str]]) -> Run:
        steps = []
        for i, names in enumerate(tools_per_step):
            steps.append(
                Step(
                    i=i,
                    state_hash_before="a",
                    state_hash_after="a",
                    tool_calls=[
                        ToolCall(id=f"t{i}{j}", name=n, args={}, args_norm={})
                        for j, n in enumerate(names)
                    ],
                )
            )
        return Run(
            run_id="synthetic",
            task_id=task.id,
            version_id="synthetic",
            version_hash="-",
            mode="sim",
            seed=0,
            steps=steps,
        )

    def state(with_event: bool) -> dict[str, list[dict[str, Any]]]:
        events = (
            [
                {
                    "id": "e9",
                    "title": "田中さんと打合せ",
                    "start": "2026-09-21T11:00",
                    "end": "2026-09-21T11:30",
                    "room": "A",
                    "attendees": "c001",
                }
            ]
            if with_event
            else []
        )
        return {
            "contacts": [],
            "events": events,
            "mails": [],
            "files": [],
            "backups": [],
            "notes": [],
        }

    in_order = evaluate_milestones(
        task,
        build([["calendar_search"], ["calendar_create"], ["checks_run"]]),
        [state(False), state(True), state(True)],
    )
    out_of_order = evaluate_milestones(
        task,
        build([["calendar_search"], ["checks_run"], ["calendar_create"]]),
        [state(False), state(False), state(True)],
    )
    unreached = evaluate_milestones(task, build([["calendar_search"]]), [state(False)])
    return {
        "in_order": in_order.score,
        "out_of_order": out_of_order.score,
        "unreached": unreached.score,
        "ok": int(
            in_order.score == 1.0
            and out_of_order.score < 1.0
            and unreached.score < out_of_order.score + 1e-9
        ),
    }


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    from scipy.stats import spearmanr

    runs = corpus()
    rows = []
    for version in VERSIONS:
        subset = [r for r in runs if r.version_id == version and r.outcome]
        score = sum(r.outcome.milestone_score for r in subset if r.outcome) / len(subset)
        rate = sum(r.passed() for r in subset) / len(subset)
        rows.append(
            {"version": version, "milestone_score": round(score, 4), "pass_rate": round(rate, 4)}
        )

    rho, p_value = spearmanr([r["milestone_score"] for r in rows], [r["pass_rate"] for r in rows])
    synthetic = synthetic_check()

    fig = plotting.scatter(
        "E4-4",
        "score_vs_pass",
        "版ごとの平均マイルストーン部分点と acceptance 合格率",
        "マイルストーン部分点",
        "合格率",
        [r["milestone_score"] for r in rows],
        [r["pass_rate"] for r in rows],
        "simulated",
    )

    metrics = {
        "spearman_rho": labeled(round(float(rho), 4)),
        "synthetic_ok": labeled(synthetic["ok"]),
        "spearman_p": labeled(round(float(p_value), 4)),
        "score_in_order": labeled(synthetic["in_order"]),
        "score_out_of_order": labeled(synthetic["out_of_order"]),
        "score_unreached": labeled(synthetic["unreached"]),
        "n_versions": labeled(len(rows)),
    }
    notes = [
        f"版ごとの値: {rows}",
        (
            "Spearman を 4 版で取っているため統計的な強さは弱い（p 値を併記した）。"
            "版を増やせば安定するが、それは順位相関の入力を増やすだけで手法の検証の中身は変わらない。"
        ),
        (
            "合成軌跡の判定: 正しい順序 = 1.0、順序違い = "
            f"{synthetic['out_of_order']}、未到達 = {synthetic['unreached']}。"
            "順序違いでは M3（after=[M2]）が部分点にならない。"
        ),
    ]
    return finalize("E4-4", metrics, METHOD, notes, figures=[("部分点と合格率", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
