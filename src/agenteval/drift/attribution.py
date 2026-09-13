"""ドリフト帰属（原典 8.8）。要因別入替（factorial）による主効果。"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

FACTORS = ("model", "prompt", "tool")


@dataclass
class AttributionResult:
    """帰属の結果。"""

    main_effects: dict[str, float]
    predicted_cause: str
    ranked: list[str] = field(default_factory=list)
    cells: dict[str, float] = field(default_factory=dict)

    def top(self, n: int = 2) -> list[str]:
        return self.ranked[:n]


def cell_key(combo: dict[str, bool]) -> str:
    """組合せのキー（例: `model=1,prompt=0,tool=0`）。"""
    return ",".join(f"{f}={int(combo[f])}" for f in FACTORS)


def combinations() -> list[dict[str, bool]]:
    """2^3 の組合せ。"""
    return [
        dict(zip(FACTORS, bits, strict=True)) for bits in itertools.product([False, True], repeat=3)
    ]


def main_effects(pass_rates: dict[str, float]) -> AttributionResult:
    """各要因を入れ替えたときの合格率変化の平均（主効果）。"""
    effects: dict[str, float] = {}
    for factor in FACTORS:
        deltas = []
        for combo in combinations():
            if combo[factor]:
                continue
            off = pass_rates.get(cell_key(combo))
            on_combo = dict(combo)
            on_combo[factor] = True
            on = pass_rates.get(cell_key(on_combo))
            if off is None or on is None:
                continue
            deltas.append(on - off)
        effects[factor] = round(sum(deltas) / len(deltas), 4) if deltas else 0.0
    ranked = sorted(FACTORS, key=lambda f: abs(effects[f]), reverse=True)
    return AttributionResult(
        main_effects=effects,
        predicted_cause=ranked[0],
        ranked=list(ranked),
        cells=dict(pass_rates),
    )


def alarm(result: AttributionResult, flake_band: float) -> dict[str, Any]:
    """フレーク帯を超える主効果があるときだけ警報する。"""
    significant = {f: e for f, e in result.main_effects.items() if abs(e) > flake_band}
    return {
        "alarm": bool(significant),
        "significant": significant,
        "flake_band": flake_band,
        "predicted_cause": result.predicted_cause if significant else None,
    }
