"""E8-8 テストのライフサイクル状態機械（原典 8.10）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.drift.lifecycle import (
    Metrics,
    State,
    TerminalState,
    log_transition,
    next_state,
    read_log,
)
from agenteval.reports import plotting

# (開始状態, 遷移を起こすメトリクス, 期待する遷移先)
TRANSITIONS: list[tuple[State, Metrics, State]] = [
    ("Created", Metrics(validated=True), "Validated"),
    ("Validated", Metrics(flake_rate=0.1), "Active"),
    ("Active", Metrics(fidelity=0.5), "Stale"),
    ("Active", Metrics(discriminative=0.02), "Saturated"),
    ("Active", Metrics(flake_rate=0.5), "Hardened"),
    ("Saturated", Metrics(retire_requested=True), "Retired"),
    ("Stale", Metrics(rerecorded=True), "Rerecorded"),
    ("Rerecorded", Metrics(fidelity=1.0), "Active"),
    ("Hardened", Metrics(flake_rate=0.1), "Active"),
]

# 条件を満たさないメトリクス（遷移しないこと）
NO_TRIGGER: list[tuple[State, Metrics]] = [
    ("Created", Metrics(validated=False)),
    ("Validated", Metrics(flake_rate=0.5)),
    ("Active", Metrics(discriminative=0.5, fidelity=1.0, flake_rate=0.1)),
    ("Saturated", Metrics(retire_requested=False)),
    ("Stale", Metrics(rerecorded=False)),
]

METHOD = """状態機械の各遷移について、その遷移を起こすメトリクスを与えて `next_state` の戻り値を確認した
（原典 8.10）。対照として、条件を満たさないメトリクスでは状態が変わらないことを確認した。
`Retired` からの遷移は例外（`TerminalState`）になることも確認した。
`data/lifecycle.jsonl` が追記型であること（書き込み後に行数が単調増加し、既存行が変わらないこと）も検査した。
この実験は LLM も run も使わない（来歴 unit）。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    results = [(start, next_state(start, m), expected) for start, m, expected in TRANSITIONS]
    transitions_pass = int(all(actual == expected for _, actual, expected in results))

    no_trigger_results = [(start, next_state(start, m)) for start, m in NO_TRIGGER]
    no_trigger_stays = int(all(start == actual for start, actual in no_trigger_results))

    try:
        next_state("Retired", Metrics())
        retired_terminal = 0
    except TerminalState:
        retired_terminal = 1

    log_path = Path("data/lifecycle_e8_8.jsonl")
    if log_path.exists():
        log_path.unlink()
    before = len(read_log(log_path))
    log_transition("T-001", "Active", "Saturated", Metrics(discriminative=0.0), log_path)
    first = read_log(log_path)
    log_transition("T-002", "Active", "Stale", Metrics(fidelity=0.5), log_path)
    second = read_log(log_path)
    append_only = int(
        before == 0 and len(first) == 1 and len(second) == 2 and second[0] == first[0]
    )

    fig = plotting.bar_compare(
        "E8-8",
        "transitions",
        "遷移検査の結果（1 = 期待どおり）",
        ["遷移", "非遷移（対照）", "Retired 終端", "追記型"],
        [transitions_pass, no_trigger_stays, retired_terminal, append_only],
        "検査結果",
        "unit",
    )

    metrics = {
        "transitions_pass": labeled(transitions_pass, "unit"),
        "retired_terminal": labeled(retired_terminal, "unit"),
        "append_only": labeled(append_only, "unit"),
        "no_trigger_stays": labeled(no_trigger_stays, "unit"),
        "n_transitions": labeled(len(TRANSITIONS), "unit"),
        "n_no_trigger": labeled(len(NO_TRIGGER), "unit"),
    }
    notes = [
        f"遷移の検査結果: {[(s, a, e) for s, a, e in results]}",
        (
            "遷移条件の閾値（fidelity < 0.8 → Stale、|d| < 0.1 → Saturated、flake_rate > 0.3 → Hardened）は"
            "`drift/lifecycle.py` に書いてある。メトリクスの計算は `drift/state_source.py` に分けてあり、"
            "遷移判断と計算が同じ関数に混ざらないようにしている（規則どおり）。"
        ),
        (
            "原典 8.10 の図に対して、この実装は Active からの分岐の優先順位（fidelity → 弁別力 → フレーク）を"
            "決め打ちしている。複数の条件が同時に成立した場合の挙動は原典に明示が無いため、実装の判断である。"
        ),
    ]
    return finalize(
        "E8-8", metrics, METHOD, notes, figures=[("遷移検査", fig)], seed=seed, provenance="unit"
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
