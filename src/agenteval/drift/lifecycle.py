"""テストの状態機械（原典 8.10）。遷移条件はメトリクスを引数に取る関数で書く。

計算（メトリクス）と遷移判断を分ける（.claude/rules/drift.md の落とし穴）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agenteval.llm.cost import REPO_ROOT

State = Literal[
    "Created", "Validated", "Active", "Saturated", "Stale", "Hardened", "Rerecorded", "Retired"
]

LIFECYCLE_PATH = REPO_ROOT / "data" / "lifecycle.jsonl"


class TerminalState(RuntimeError):
    """Retired から遷移しようとした。"""


@dataclass(frozen=True)
class Metrics:
    """遷移判断に使うメトリクス。"""

    discriminative: float = 0.5
    fidelity: float = 1.0
    age_days: int = 0
    validated: bool = False
    flake_rate: float = 0.0
    rerecorded: bool = False
    retire_requested: bool = False


def next_state(state: State, m: Metrics) -> State:
    """原典 8.10 の遷移。条件を満たさなければ同じ状態を返す。"""
    if state == "Retired":
        raise TerminalState("Retired は終端状態で、そこからは遷移しない")
    if state == "Created":
        return "Validated" if m.validated else "Created"
    if state == "Validated":
        return "Active" if m.flake_rate <= 0.2 else "Validated"
    if state == "Active":
        if m.fidelity < 0.8:
            return "Stale"
        if abs(m.discriminative) < 0.1:
            return "Saturated"
        if m.flake_rate > 0.3:
            return "Hardened"
        return "Active"
    if state == "Saturated":
        return "Retired" if m.retire_requested else "Saturated"
    if state == "Stale":
        return "Rerecorded" if m.rerecorded else "Stale"
    if state == "Rerecorded":
        return "Active" if m.fidelity >= 0.8 else "Stale"
    if state == "Hardened":
        return "Active" if m.flake_rate <= 0.2 else "Hardened"
    return state


def log_transition(
    test_id: str, before: State, after: State, m: Metrics, path: Path | None = None
) -> None:
    """`data/lifecycle.jsonl` に追記する（追記型。書き換えない）。"""
    target = path or LIFECYCLE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "test_id": test_id,
        "from": before,
        "to": after,
        "metrics": m.__dict__,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log(path: Path | None = None) -> list[dict[str, Any]]:
    """遷移ログを読む。"""
    target = path or LIFECYCLE_PATH
    if not target.exists():
        return []
    return [
        json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def tick_all(path: Path | None = None) -> dict[str, Any]:
    """保存済みメトリクスから全テストの状態を 1 段進める（CLI から呼ぶ）。"""
    from agenteval.drift.state_source import current_states, metrics_for

    moved = []
    for test_id, state in current_states().items():
        m = metrics_for(test_id)
        after = next_state(state, m)
        if after != state:
            log_transition(test_id, state, after, m, path)
            moved.append({"test_id": test_id, "from": state, "to": after})
    return {"moved": moved, "count": len(moved)}
