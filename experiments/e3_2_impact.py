"""E3-2 意味的影響推定による選択（原典 3.3）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, flake_band, flipped_tasks, labeled

from agenteval.llm.client import LLMClient
from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
from agenteval.pts import impact as impact_mod
from agenteval.reports import plotting

METHOD = """v01 → v02_dateformat のプロンプト差分（section: date_format）から影響タグを列挙し、
タスクの tags・category・kind を文字 2〜3-gram の TF-IDF でベクトル化して、コサイン類似が閾値 0.15 以上の
タスクを選んだ（原典 3.3）。反転は ADR-009 の定義（対応する反復での不一致率 > フレーク帯）。
対照は v01 → v02b_tone（文体だけの差分、run は作らない）で、同じ手順の selected_ratio を見る。
カバレッジ単独（section: date_format を参照するタスク）の precision と比較した。
live が使えないため、タグ列挙は LLM ではなく差分本文からの決定的抽出（impact.tags_offline）を使い、
来歴を simulated とした。live で Haiku に列挙させる経路（impact.tags_live）は実装済みで未実行。"""


def precision(selected: set[str], flipped: set[str]) -> float:
    return round(len(selected & flipped) / len(selected), 4) if selected else 0.0


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")
    flipped = flipped_tasks(runs, "v01_baseline", "v02_dateformat", bands)

    client = LLMClient(mode="auto" if live else "sim", run_id="E3-2") if live else None
    prompt_change = next(
        c for c in change_mod.diff_versions("v01_baseline", "v02_dateformat") if c.kind == "prompt"
    )
    estimate = impact_mod.estimate(prompt_change, client)
    selected = set(estimate.selected)

    control_change = next(
        c for c in change_mod.diff_versions("v01_baseline", "v02b_tone") if c.kind == "prompt"
    )
    control = impact_mod.estimate(control_change, client)

    coverage = coverage_mod.coverage_map([r for r in runs if r.version_id == "v01_baseline"])
    coverage_candidates = coverage_mod.candidates(prompt_change, coverage)

    recall = round(len(selected & flipped) / len(flipped), 4) if flipped else 0.0
    impact_precision = precision(selected, flipped)
    coverage_precision = precision(coverage_candidates, flipped)

    fig = plotting.bar_compare(
        "E3-2",
        "precision",
        "反転タスクに対する precision（影響推定 vs カバレッジ単独）",
        ["影響推定", "カバレッジ単独"],
        [impact_precision, coverage_precision],
        "precision",
        "simulated",
    )

    metrics = {
        "recall_of_flipped": labeled(recall),
        "control_selected_ratio": labeled(control.selected_ratio),
        "precision_gain_vs_coverage": labeled(round(impact_precision - coverage_precision, 4)),
        "impact_precision": labeled(impact_precision),
        "coverage_precision": labeled(coverage_precision),
        "n_flipped": labeled(len(flipped)),
        "n_selected": labeled(len(selected)),
        "selected_ratio": labeled(estimate.selected_ratio),
    }
    notes = [
        f"列挙された影響タグ: {estimate.tags}",
        f"対照（v02b_tone）のタグ: {control.tags}、選択率 {control.selected_ratio}",
        f"反転タスク: {sorted(flipped)}",
        f"影響推定が選んだタスク: {sorted(selected)}",
        (
            "タグ列挙は決定的抽出（KEYWORD_TAGS）なので、LLM の意味理解の寄与は検証できていない。"
            "この実験が確かめたのは「タグ → TF-IDF 照合 → 選択」の経路が機能することまで。"
        ),
    ]
    return finalize("E3-2", metrics, METHOD, notes, figures=[("precision 比較", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
