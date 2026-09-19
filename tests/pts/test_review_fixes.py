"""レビュー（ADR-016〜ADR-023）で直した評価ロジックの回帰テスト。

いずれも API を呼ばない。「直した挙動が将来また壊れないこと」だけを固定する。
"""

from __future__ import annotations

import math

import pytest

from agenteval.core.schema import Outcome, Run, Step, ToolCall, ToolResult, Usage
from agenteval.drift import discriminative as disc_mod
from agenteval.pts import kpi as kpi_mod
from agenteval.pts import selector as selector_mod
from agenteval.pts import sprt as sprt_mod


def _run(task_id: str, version_id: str, repeat: int, passed: bool) -> Run:
    step = Step(
        i=0,
        state_hash_before="a",
        state_hash_after="b",
        tool_calls=[ToolCall(id="t0", name="finish", args={}, args_norm={})],
        tool_results=[ToolResult(id="t0", content="{}")],
        usage=Usage(input_tokens=10, output_tokens=1),
        context_tokens=10,
    )
    return Run(
        run_id=f"{task_id}-{version_id}-{repeat}",
        task_id=task_id,
        version_id=version_id,
        version_hash="h",
        mode="sim",
        seed=0,
        repeat=repeat,
        steps=[step],
        outcome=Outcome(passed=passed),
    )


# --- ADR-023 ---------------------------------------------------------------
def test_fixed_n_uses_one_sample_null_variance() -> None:
    """一標本検定なので α 項の分散は帰無仮説下の p0(1-p0)。"""
    from scipy.stats import norm

    p0, p1, alpha, beta = 0.5, 0.9, 0.05, 0.10
    z_a, z_b = float(norm.ppf(1 - alpha)), float(norm.ppf(1 - beta))
    expected = math.ceil(
        ((z_a * math.sqrt(p0 * (1 - p0)) + z_b * math.sqrt(p1 * (1 - p1))) / (p1 - p0)) ** 2
    )
    assert sprt_mod.fixed_n_for_power(p0, p1, alpha, beta) == expected == 10
    # 改訂前（プール分散）は過小になる。併記用に残してある。
    assert sprt_mod.fixed_n_pooled_legacy(p0, p1, alpha, beta) == 9


# --- ADR-016 ---------------------------------------------------------------
def test_paired_disagreement_counts_as_flip_without_threshold() -> None:
    """sim は対応のある決定的比較なので、不一致 1 件で反転と数える。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))
    from _common import flake_band, flipped_tasks

    runs = []
    for repeat in range(10):
        # base は 5 勝 5 敗（合格率 0.5 → 改訂前の帯は約 0.31 と大きい）
        runs.append(_run("T-x", "base", repeat, repeat < 5))
        # target は 1 反復だけ結果が変わる
        runs.append(_run("T-x", "target", repeat, repeat < 5 if repeat != 9 else True))
    assert flipped_tasks(runs, "base", "target") == {"T-x"}
    # 改訂前の帯を明示的に渡すと、同じ不一致が反転として数えられない
    bands = flake_band(runs, "base")
    assert bands["T-x"] > 0.3
    assert flipped_tasks(runs, "base", "target", bands) == set()


# --- ADR-018 ---------------------------------------------------------------
def test_escape_rate_denominator_is_failures_not_flips() -> None:
    """原典 3.8: Escape(S) = |F_full \\ S| / |F_full|。"""
    selection = selector_mod.Selection(
        selected=["T-1"], skipped=["T-2", "T-3"], budget=0.0, expected_escape=0.0
    )
    assert kpi_mod.escape_rate(selection, {"T-1", "T-2"}) == 0.5
    assert kpi_mod.escape_rate(selection, set()) == 0.0
    # 反転ベースは別関数として残っている
    assert kpi_mod.escape_rate_flips(selection, {"T-2", "T-3"}) == 1.0


def test_escape_over_failures_averages_across_repeats() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))
    from _common import escape_over_failures, failures_by_repeat

    runs = [_run("T-1", "v", 0, False), _run("T-2", "v", 0, True), _run("T-1", "v", 1, True)]
    failures = failures_by_repeat(runs, "v")
    assert failures == {0: {"T-1"}, 1: set()}
    # 失敗が無い反復は分母が 0 なので平均から除く
    assert escape_over_failures(set(), failures) == 1.0
    assert escape_over_failures({"T-1"}, failures) == 0.0


# --- ADR-020 ---------------------------------------------------------------
@pytest.mark.parametrize(
    "build",
    [
        lambda ids, runs: selector_mod.select_random(ids, runs, 0.05, seed=1),
        lambda ids, runs: selector_mod.select_recent_failures(ids, runs, 0.05),
    ],
)
def test_controls_include_the_inviolable_set(build: object) -> None:
    """対照にも不可侵集合が入る（片方だけ制約が掛かっていると対照にならない）。"""
    from agenteval.core.registry import get_task, load_tasks

    task_ids = sorted(load_tasks())
    inviolable = {t for t in task_ids if get_task(t).risk == "inviolable"}
    assert inviolable, "不可侵タスクが定義されていない"
    runs = [_run(t, "v01_baseline", 0, True) for t in task_ids]
    selection = build(task_ids, runs)  # type: ignore[operator]
    assert inviolable <= set(selection.selected)


# --- 飽和判定（`.claude/rules/drift.md` に合わせる） ------------------------
def test_saturation_requires_every_version_above_threshold() -> None:
    """プールした合格率ではなく、全版が 0.95 超であること。"""
    runs = [_run("T-901", "v01_baseline", r, True) for r in range(10)]
    runs += [_run("T-901", "v03_noverify", r, r < 5) for r in range(10)]
    result = disc_mod.evaluate("T-901", runs)
    assert not result.saturated, "v03 が 0.5 なのに飽和と判定されている"
