"""E3-4 テストピラミッドの段判定（原典 3.5）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, flake_band, flipped_tasks, labeled

from agenteval.pts import change as change_mod
from agenteval.pts import pyramid as pyramid_mod
from agenteval.reports import plotting

PLANTED = {
    "v02_dateformat": "prompt",
    "v06_toolschema_v2": "tool",
    "v09_model_swap": "model",
}

METHOD = """各植込み版について `Change` を計算し、`decide_level` が返す段と、実際に反転が観測された
最小の段（`observed_min_level`）を比較した（原典 3.5）。反転の定義は ADR-009。
対照は `Change(kind=code)`（ツールラッパーのリファクタ。版ハッシュは変わらない）で、
L0〜L1 で停止し、非フレーク反転が 0 であることを確認した。
この例示環境では L2〜L4 を「軌跡の実行」で代表しているため、段の粒度はレポートより粗い。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    runs = corpus()
    bands = flake_band(runs, "v01_baseline")

    rows = []
    ok = 0
    for version_id, kind in PLANTED.items():
        merged = change_mod.merge(change_mod.diff_versions("v01_baseline", version_id))
        assert merged is not None
        flipped = flipped_tasks(runs, "v01_baseline", version_id, bands)
        decided = pyramid_mod.decide_level(merged)
        observed = pyramid_mod.observed_min_level(bool(flipped), kind)
        at_least = pyramid_mod.at_least(decided, observed)
        ok += int(at_least)
        rows.append(
            {
                "version": version_id,
                "change_kind": merged.kind,
                "decided": decided,
                "observed_min": observed,
                "n_flipped": len(flipped),
                "ok": at_least,
            }
        )

    control = change_mod.synthetic("code", set())
    control_level = pyramid_mod.decide_level(control)
    # 対照: 版ハッシュが変わらない変更なので、同じ版どうしの比較になる（反転は定義上 0）
    control_flips = flipped_tasks(runs, "v01_baseline", "v01_baseline", bands)

    fig = plotting.bar_compare(
        "E3-4",
        "levels",
        "判定した段（L の番号）と反転が観測された最小の段",
        [f"{r['version']}\n{r['decided']}/{r['observed_min']}" for r in rows],
        [pyramid_mod.level_index(r["decided"]) for r in rows],  # type: ignore[arg-type]
        "段のインデックス",
        "simulated",
    )

    metrics = {
        "level_ok_rate": labeled(round(ok / len(rows), 4)),
        "control_nonflake_flips": labeled(len(control_flips)),
        "control_level": labeled(control_level),
        "n_planted": labeled(len(rows)),
    }
    notes = [
        f"段の判定結果: {rows}",
        f"対照（kind=code）の段: {control_level}（L1 で停止）。版ハッシュが変わらないため反転は定義上 0。",
        (
            "反転が 0 件の版では observed_min が L1 に落ちるため、decided ≥ observed が自動的に成り立つ。"
            "つまりこの基準は「過小な段を選ばないこと」しか確かめておらず、"
            "「過大な段を選ばないこと」は確かめていない。"
        ),
    ]
    return finalize("E3-4", metrics, METHOD, notes, figures=[("段判定", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
