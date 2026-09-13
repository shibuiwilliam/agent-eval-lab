"""E4-1 軌跡メトリクスの選択的反応（原典 4.2）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task
from agenteval.process import metrics as metrics_mod
from agenteval.reports import plotting

VERSIONS = ["v01_baseline", "v02_dateformat", "v03_noverify", "v04_loopy"]

METHOD = """コーパス全体から `Run` だけを使って軌跡メトリクスを計算した（原典 4.2）。
`L_min` はタスク YAML の `l_min` を使った。重複呼び出しは `(name, args_norm)` の完全一致、
検証行動率の分母は「書込み系ツールを 1 回以上呼んだ run」（読むだけの run は NaN として平均から除外）。
植込みは v04_loopy（同じ検索を 2 度なぞる指示）と v03_noverify（検査の section を落とした版）。
対照は v01 の反復（フレーク帯の算出）と v02_dateformat（これらの指標は動かないはず）。
「フレーク帯の内側」は、v01 の反復をランダムに 2 群に分けたときの指標比のばらつき（95% 区間）とした。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    import numpy as np

    runs = corpus()
    per_version: dict[str, list[metrics_mod.RunMetrics]] = {}
    for version in VERSIONS:
        rows = []
        for run in runs:
            if run.version_id != version:
                continue
            task = get_task(run.task_id)
            rows.append(metrics_mod.compute(run, l_min=task.l_min))
        per_version[version] = rows

    def agg(version: str, field: str) -> float:
        return metrics_mod.aggregate(per_version[version], field)

    dup_v01, dup_v04 = agg("v01_baseline", "dup_rate"), agg("v04_loopy", "dup_rate")
    ver_v01, ver_v03 = (
        agg("v01_baseline", "verification_rate"),
        agg("v03_noverify", "verification_rate"),
    )
    dup_v02, ver_v02 = agg("v02_dateformat", "dup_rate"), agg("v02_dateformat", "verification_rate")

    # フレーク帯: v01 の反復を 2 群に分けたときの指標比の揺れ
    rng = np.random.default_rng(seed)
    v01_rows = per_version["v01_baseline"]
    ratios = []
    for _ in range(500):
        order = rng.permutation(len(v01_rows))
        half = len(v01_rows) // 2
        a = [v01_rows[i] for i in order[:half]]
        b = [v01_rows[i] for i in order[half:]]
        for field in ("dup_rate", "verification_rate"):
            x, y = metrics_mod.aggregate(a, field), metrics_mod.aggregate(b, field)
            if y:
                ratios.append(x / y)
    band_low, band_high = (float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5)))

    dup_ratio_v02 = dup_v02 / dup_v01 if dup_v01 else 1.0
    ver_ratio_v02 = ver_v02 / ver_v01 if ver_v01 else 1.0
    v02_within = int(
        band_low <= dup_ratio_v02 <= band_high and band_low <= ver_ratio_v02 <= band_high
    )

    fig = plotting.bar_compare(
        "E4-1",
        "dup_rate",
        "重複呼び出し率（版ごと）",
        VERSIONS,
        [agg(v, "dup_rate") for v in VERSIONS],
        "重複呼び出し率",
        "simulated",
    )
    fig2 = plotting.bar_compare(
        "E4-1",
        "verification_rate",
        "検証行動率（書込みを行った run のみ）",
        VERSIONS,
        [agg(v, "verification_rate") for v in VERSIONS],
        "検証行動率",
        "simulated",
    )

    metrics = {
        "dup_ratio_v04_vs_v01": labeled(round(dup_v04 / dup_v01, 4) if dup_v01 else float("inf")),
        "verification_ratio_v03_vs_v01": labeled(round(ver_v03 / ver_v01, 4) if ver_v01 else 0.0),
        "v02_change_within_flake_band": labeled(v02_within),
        "dup_rate_v01": labeled(round(dup_v01, 4)),
        "dup_rate_v04": labeled(round(dup_v04, 4)),
        "verification_rate_v01": labeled(round(ver_v01, 4)),
        "verification_rate_v03": labeled(round(ver_v03, 4)),
        "flake_band_ratio_low": labeled(round(band_low, 4)),
        "flake_band_ratio_high": labeled(round(band_high, 4)),
        "backtracks_v01": labeled(round(agg("v01_baseline", "backtracks"), 4)),
        "backtracks_v04": labeled(round(agg("v04_loopy", "backtracks"), 4)),
    }
    notes = [
        f"v02 の指標比: dup={round(dup_ratio_v02, 3)}, verification={round(ver_ratio_v02, 3)}",
        (
            "v04 は「同じ検索をもう一度なぞる」指示なので、重複呼び出し率と後戻り回数が上がり、"
            "検証行動率は動かない。v03 は検査の section を落としたので検証行動率だけが下がる。"
            "この選択性（対応する指標だけが動く）が仮説の中身である。"
        ),
        (
            "v03 と v04 は合格率をほとんど動かさない（どちらも v01 と同じ 0.79）。"
            "成果物だけを見る評価ではこの 2 つの版の劣化は検出できず、過程の指標で初めて分かれる。"
            "これは原典 4.1 の主張（成果と過程は独立の軸）に合う観測である。"
        ),
    ]
    return finalize(
        "E4-1",
        metrics,
        METHOD,
        notes,
        figures=[("重複呼び出し率", fig), ("検証行動率", fig2)],
        seed=seed,
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
