"""ライフサイクルの入力（現在状態とメトリクス）をコーパスから作る。

`lifecycle.py` は遷移判断だけを行い、メトリクス計算はここに置く（責務を分ける）。
"""

from __future__ import annotations

from agenteval.core.store import load_corpus
from agenteval.drift.lifecycle import Metrics, State, read_log


def current_states() -> dict[str, State]:
    """遷移ログの最新状態。ログが無ければ全テスト Created。"""
    from agenteval.core.registry import load_tasks

    states: dict[str, State] = {task_id: "Created" for task_id in load_tasks()}
    for record in read_log():
        states[record["test_id"]] = record["to"]
    return states


def metrics_for(test_id: str) -> Metrics:
    """コーパスからテスト 1 件のメトリクスを作る。"""
    from agenteval.drift.discriminative import discriminative_power
    from agenteval.process.consistency import flake_rate

    runs = [r for r in load_corpus() if r.task_id == test_id]
    if not runs:
        return Metrics()
    d = discriminative_power(runs)
    return Metrics(
        discriminative=d,
        fidelity=1.0,
        age_days=0,
        validated=True,
        flake_rate=flake_rate(runs),
    )
