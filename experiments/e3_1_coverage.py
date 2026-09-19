"""E3-1 軌跡カバレッジ依存グラフによる候補絞り込み（原典 3.2）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, flake_band, flipped_tasks, labeled

from agenteval.pts import change as change_mod
from agenteval.pts import coverage as coverage_mod
from agenteval.reports import plotting

METHOD = """v01_baseline の全 run から `U(t)`（呼んだツール・参照した prompt section・触れた資源）を抽出し、
`Change` の触る要素 `C(Δ)` と交差するタスクを候補集合 `T_cand(Δ)` とした（原典 3.2）。
植込みは v01 → v06_toolschema_v2（`calendar_search` の引数名変更、`kind=tool`）。
反転は v01 と v06 の合格率が 0.5 をまたいだタスクとし、v01 の反復で合否が割れたタスク（フレーク帯）は
反転と数えない。対照は合成変更 `Change(kind=tool, components={mail_send})` で、候補に選ばれたタスクが
別の版（v03）の軌跡でも本当に `mail_send` を呼んでいるかを照合した（選択基準と実測の突き合わせ）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    v01 = [r for r in runs if r.version_id == "v01_baseline"]
    coverage = coverage_mod.coverage_map(v01)

    changes = change_mod.diff_versions("v01_baseline", "v06_toolschema_v2")
    tool_change = next(c for c in changes if c.kind == "tool")
    bands = flake_band(runs, "v01_baseline")
    # ADR-016: sim は対応のある決定的比較なので閾値を置かない（不一致が 1 件でもあれば反転）。
    flipped = flipped_tasks(runs, "v01_baseline", "v06_toolschema_v2")
    flipped_legacy = flipped_tasks(runs, "v01_baseline", "v06_toolschema_v2", bands)

    candidates = coverage_mod.candidates(tool_change, coverage)
    recall = len(flipped & candidates) / len(flipped) if flipped else 1.0
    ratio = coverage_mod.candidate_ratio(tool_change, coverage)

    control = change_mod.synthetic("tool", {"mail_send"})
    control_candidates = coverage_mod.candidates(control, coverage)
    v03_tools = {r.task_id: set(r.tool_names()) for r in runs if r.version_id == "v03_noverify"}
    false_candidates = sorted(
        t for t in control_candidates if "mail_send" not in v03_tools.get(t, set())
    )

    fig = plotting.bar_compare(
        "E3-1",
        "candidate_ratio",
        "変更の種類ごとの候補割合（全 46 タスク中）",
        ["tool: calendar_search\n(v06 植込み)", "tool: mail_send\n(対照)", "prompt: date_format"],
        [
            ratio,
            coverage_mod.candidate_ratio(control, coverage),
            coverage_mod.candidate_ratio(change_mod.synthetic("prompt", {"date_format"}), coverage),
        ],
        "候補割合",
        "simulated",
        threshold=0.6,
    )

    metrics = {
        "recall_of_flipped": labeled(round(recall, 4)),
        "candidate_ratio": labeled(ratio),
        "control_false_candidates": labeled(len(false_candidates)),
        "n_flipped": labeled(len(flipped)),
        "mean_flake_band": labeled(round(sum(bands.values()) / len(bands), 4)),
        "n_flipped_legacy_band": labeled(len(flipped_legacy)),
        "n_candidates": labeled(len(candidates)),
        "n_tasks": labeled(len(coverage)),
        "control_candidate_ratio": labeled(coverage_mod.candidate_ratio(control, coverage)),
    }
    notes = [
        f"反転タスク: {sorted(flipped)}",
        f"フレーク帯（v01 の反復から出した二項の 95% 半幅）の平均: {round(sum(bands.values()) / len(bands), 4)}",
        f"対照（mail_send）の候補: {sorted(control_candidates)}",
        (
            "候補集合はプロンプト section の「参照」をツール対応表で近似している（coverage.py の SECTION_TOOLS）。"
            "実際に LLM がその section を読んだかは観測できないので、これは近似であり原典 3.2 そのものではない。"
        ),
        "来歴は simulated（ADR-008 のシミュレート・エージェントで作ったコーパス）。live での再確認は未実施。",
    ]
    return finalize("E3-1", metrics, METHOD, notes, figures=[("候補割合", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
