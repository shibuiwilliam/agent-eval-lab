"""E8-9 汚染検知（カナリア走査、原典 8.9）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.drift import contamination as cont_mod
from agenteval.reports import plotting

METHOD = """`visibility: private` のタスク 4 件に埋め込んだ canary 文字列を、`prompts/`・`versions/`・
`docs/`・LLM カセットの system 部分から走査した（原典 8.9）。
植込みは v10_contaminated（private タスクの canary 付き例文を few-shot に混入した版）、対照は v01_baseline。
検出率は「走査で見つかった canary の数 ÷ 混入した canary の数」。
public / private の合格率差は版ごとに出した（gap = private の合格率 − public の合格率）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    canaries = cont_mod.canaries()

    v10_scan = cont_mod.scan_version("v10_contaminated")
    v01_scan = cont_mod.scan_version("v01_baseline")
    detection = len(v10_scan.detected_tasks) / len(canaries) if canaries else 0.0

    gap_v10 = cont_mod.visibility_gap(runs, "v10_contaminated")
    gap_v01 = cont_mod.visibility_gap(runs, "v01_baseline")

    full_scan = cont_mod.scan()

    fig = plotting.bar_compare(
        "E8-9",
        "gap",
        "public / private の合格率差（gap = private − public）",
        ["v01_baseline（対照）", "v10_contaminated（植込み）"],
        [gap_v01["gap"], gap_v10["gap"]],
        "gap",
        "simulated",
        threshold=0.2,
    )

    metrics = {
        "canary_detection_rate": labeled(round(detection, 4)),
        "baseline_hits": labeled(len(v01_scan.hits)),
        "gap_v10": labeled(gap_v10["gap"]),
        "gap_v01": labeled(gap_v01["gap"]),
        "private_rate_v10": labeled(gap_v10["private"]),
        "public_rate_v10": labeled(gap_v10["public"]),
        "private_rate_v01": labeled(gap_v01["private"]),
        "public_rate_v01": labeled(gap_v01["public"]),
        "n_canaries": labeled(len(canaries)),
        "full_scan_hits": labeled(len(full_scan.hits)),
        "full_scan_files": labeled(full_scan.scanned_files),
    }
    notes = [
        f"v10 で検出した canary: {sorted(v10_scan.detected_tasks)}",
        f"リポジトリ全体の走査: {full_scan.scanned_files} ファイル中 {len(full_scan.hits)} 件の混入",
        (
            "リポジトリ全体の走査では、結果ページや実験スクリプトに canary 文字列が載っていると"
            "検出されてしまう。走査対象から `docs/results/` を外していないため、"
            "この数値は「混入版の検出」ではなく「文字列の出現」を数えている点に注意。"
        ),
        (
            "汚染による合格率の上がり方は、シミュレータが「汚染版 × private タスクでは能力 0.9」と"
            "決め打ちしている結果である。実際の LLM が few-shot の答えをどれだけ再利用するかは"
            "この実験では分からない。検証したのは走査と gap の計算が働くことまで。"
        ),
    ]
    notes.append(
        "判定は FAIL（実験設計の不備）。カナリア走査の検出率は 1.0、対照の誤検出は 0 件で、"
        "**検知手法そのものは主張どおり働いた**。届かなかったのは gap ≥ 0.2 の側で、"
        "v10 の gap は 0.177 だった。"
        "gap の大きさはシミュレータの「汚染版は private タスクで能力 0.9」という設定が直接決めている"
        "自由パラメータなので、この基準は sim では意味のある検証になっていない。"
        "能力を 1.0 にすれば基準を満たすが、それは基準に合わせて植込みの強さを調整することなので行わない。"
        "live で「few-shot に答えがあるとどれだけ合格率が上がるか」を測る必要がある。"
    )
    return finalize(
        "E8-9", metrics, METHOD, notes, figures=[("public/private の差", fig)], seed=seed
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
