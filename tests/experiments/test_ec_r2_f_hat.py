"""EC-R2 の `c13_f_hat`（ADR-045）: c13 の bugfix 試行の F の頻度のタスク等重み平均、来歴 live。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agenteval.core.coding_types import ACC_FINISHED, ACC_VISIBLE
from agenteval.llm.cost import REPO_ROOT

EXPERIMENT_DIR = REPO_ROOT / "experiments"
SIBLING = Path(__file__).parent / "test_coding_experiments.py"


def _load(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(EXPERIMENT_DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(EXPERIMENT_DIR))


@pytest.fixture(scope="module")
def r2() -> ModuleType:
    return _load(EXPERIMENT_DIR / "ec_r2_retry.py", "ec_r2_retry")


def test_f_hat_is_task_equal_weighted_mean(r2: ModuleType) -> None:
    # タスク a: 6 試行中 F が 3、タスク b: 2 試行中 F が 0、タスク c: 4 試行中 F が 4
    trials = {
        "a": ["F", "F", "F", "S", "S", "W"],
        "b": ["S", "W"],
        "c": ["F", "F", "F", "F"],
    }
    assert r2.f_hat(trials) == pytest.approx((0.5 + 0.0 + 1.0) / 3)  # 試行数で重み付けしない
    assert r2.f_hat({"a": ["S", "W"]}) == 0.0


def test_compute_reports_c13_f_hat_labeled_live_from_the_trial_pool(r2: ModuleType) -> None:
    sibling = _load(SIBLING, "_sibling_coding_experiments")
    runs, _, _ = sibling._synthetic_corpus(n_tasks=10, w13=0.3, seed=8)
    hashes = {r.run_id: f"h{i}" for i, r in enumerate(x for v in runs.values() for x in v)}
    out = r2.compute(runs, hashes, seed=3, n_boot=20)
    metric = out["metrics"]["c13_f_hat"]
    assert metric["provenance"] == "live"
    # 独立の計算: run の未完了または可視テスト不合格（= F）の頻度をタスクごとに数えて等重み平均する
    by_task: dict[str, list[bool]] = {}
    for run in runs["c13_minimal_fix"]:
        assert run.outcome is not None
        acc = run.outcome.acceptance_results
        by_task.setdefault(run.task_id, []).append(
            not (acc[ACC_FINISHED] and acc[ACC_VISIBLE])  # EC-R1 の分類: 未完了・可視不合格は F
        )
    expected = sum(sum(v) / len(v) for v in by_task.values()) / len(by_task)
    assert metric["value"] == pytest.approx(expected)
    assert 0.0 < metric["value"] < 1.0
