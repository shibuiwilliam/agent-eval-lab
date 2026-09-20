"""学習済み PTS（合成変更・特徴量・モデル）の単体テスト。API を呼ばない。"""

from __future__ import annotations

import numpy as np
import pytest

from agenteval.core.schema import Change, Outcome, Run, Step, ToolCall, ToolResult, Usage
from agenteval.pts import dataset as ds
from agenteval.pts import model as model_mod
from agenteval.pts import selector as selector_mod
from agenteval.pts import synthetic


def _run(task_id: str, version_id: str, repeat: int, passed: bool, tools: list[str]) -> Run:
    steps = [
        Step(
            i=i,
            state_hash_before="a",
            state_hash_after="b",
            tool_calls=[ToolCall(id=f"t{i}", name=name, args={}, args_norm={})],
            tool_results=[ToolResult(id=f"t{i}", content="{}")],
            usage=Usage(input_tokens=100, output_tokens=10),
            context_tokens=100,
        )
        for i, name in enumerate(tools)
    ]
    return Run(
        run_id=f"{task_id}|{version_id}|{repeat}",
        task_id=task_id,
        version_id=version_id,
        version_hash="h",
        mode="sim",
        seed=0,
        repeat=repeat,
        steps=steps,
        outcome=Outcome(passed=passed, milestone_score=1.0 if passed else 0.0),
    )


def test_synthetic_changes_are_distinct_and_typed() -> None:
    """版を変える合成変更は互いに違う版ハッシュを持ち、族と種類が付いている。"""
    changes = synthetic.generate()
    assert len(changes) >= 30
    base = synthetic.get_version("v01_baseline").hash()
    versioned = [c for c in changes if c.family != "fault"]
    hashes = {c.version.hash() for c in versioned}
    assert len(hashes) == len(versioned), "版ハッシュが重複している = 同じ版を 2 回作っている"
    for change in changes:
        assert change.kind in ("prompt", "tool", "config", "model")
        assert change.components
        if change.family != "fault":
            assert change.version.hash() != base, f"{change.id} が base と同じ版になっている"


def test_environmental_faults_do_not_change_the_version_hash() -> None:
    """障害注入は**版ハッシュに現れない**。

    3 章の変更分類は版（model + prompt + tools + config）のハッシュを見るので、
    「外部サービスが壊れた」種類の変更は版差分からは検出できない。
    この実験ではツール障害を `Change(kind="tool", components={tool})` として明示的に作っている。
    """
    base = synthetic.get_version("v01_baseline").hash()
    faults = [c for c in synthetic.generate() if c.family == "fault"]
    assert faults
    assert all(c.version.hash() == base for c in faults)
    assert all(c.change().components for c in faults)


def test_fault_changes_carry_an_injector() -> None:
    """障害注入は ADR-026 どおり `blocks()` / `block()` で実行そのものを止める。"""
    changes = {c.id: c for c in synthetic.generate()}
    fault = changes["syn_fault_calendar_search"]
    injector = fault.injector()
    assert injector is not None
    assert injector.blocks(0, "calendar_search")
    assert not injector.blocks(0, "mail_search"), "別のツールは止めない"
    payload, is_error = injector.block(0, "calendar_search")
    assert is_error and "error" in payload
    # `apply` は応答を加工する障害のためのもので、`error` では何もしない
    assert injector.apply(0, "calendar_search", {"events": []}) == ({"events": []}, False)


def test_config_changes_are_separable_in_feature_space() -> None:
    """`max_steps=3` と `=6` が同じベクトルにならないこと（学習可能性の前提）。"""
    changes = {c.id: c for c in synthetic.generate()}
    runs = [
        _run("T-001", "v01_baseline", r, True, ["calendar_search", "calendar_create"])
        for r in range(3)
    ]
    stats = ds.BaselineStats.build(runs)
    tight = ds.static_features(
        "T-001", changes["syn_maxsteps_3"].change(), stats, changes["syn_maxsteps_3"].version
    )
    loose = ds.static_features(
        "T-001", changes["syn_maxsteps_6"].change(), stats, changes["syn_maxsteps_6"].version
    )
    assert tight != loose
    assert tight["max_steps_headroom"] < loose["max_steps_headroom"]


def test_label_uses_paired_repeats() -> None:
    """回帰 = ベースラインが合格した反復で対象版が落ちたこと（対応のある比較）。"""

    class _Stub:
        id = "c1"
        family = "prompt"
        version = None

        def change(self) -> Change:
            return Change(kind="prompt", base="v01_baseline", target="c1", components={"role"})

    runs = [
        _run("T-001", "v01_baseline", 0, True, ["calendar_search"]),
        _run("T-001", "v01_baseline", 1, False, ["calendar_search"]),
        _run("T-001", "c1", 0, False, ["calendar_search"]),  # 回帰
        _run("T-001", "c1", 1, False, ["calendar_search"]),  # 元から落ちている → 回帰ではない
    ]
    rows, _ = ds.build_rows(runs, [_Stub()])
    assert len(rows) == 1
    assert rows[0].label_regressed == 1
    regs = ds.regressions_by_repeat(runs, "c1")
    assert regs == {0: {"T-001"}, 1: set()}


def test_history_features_only_see_the_training_pool() -> None:
    """履歴特徴は渡された行からしか作らない（hold-out のラベルが漏れない）。"""
    change = Change(kind="prompt", base="b", target="t", components={"role"})
    row = ds.Row(
        change_id="c9",
        family="prompt",
        task_id="T-001",
        label_regressed=1,
        label_failed=1,
        cost=1.0,
        static={"change_kind_prompt": 1.0, "touches_change": 1.0},
    )
    assert ds.history_features("T-001", change, [])["hist_regress_rate"] == 0.0
    assert ds.history_features("T-001", change, [row])["hist_regress_rate"] == 1.0
    assert ds.history_features("T-002", change, [row])["hist_n_changes"] == 0.0


def test_select_by_risk_orders_by_score_per_cost_and_keeps_inviolable() -> None:
    scores = {"a": 0.9, "b": 0.5, "c": 0.1, "inv": 0.0}
    costs = dict.fromkeys(scores, 1.0)
    sel = selector_mod.select_by_risk(scores, costs, budget_ratio=0.75, inviolable=["inv"])
    assert "inv" in sel.selected
    assert sel.selected.index("a") < sel.selected.index("b")
    assert "c" not in sel.selected


def test_select_by_risk_reserves_an_exploration_slot() -> None:
    scores = {f"t{i}": 1.0 - i / 20 for i in range(20)}
    costs = dict.fromkeys(scores, 1.0)
    plain = selector_mod.select_by_risk(scores, costs, budget_ratio=0.5)
    explored = selector_mod.select_by_risk(scores, costs, budget_ratio=0.5, epsilon=0.2, seed=1)
    assert sum(1 for r in explored.reasons.values() if r == "epsilon") > 0
    assert sum(1 for r in plain.reasons.values() if r == "epsilon") == 0


def test_recall_at_budget_is_monotone_in_budget() -> None:
    scores = {f"t{i}": float(i) for i in range(10)}
    costs = dict.fromkeys(scores, 1.0)
    positives = {"t9", "t8", "t0"}
    recalls = [model_mod.recall_at_budget(scores, positives, costs, b)[0] for b in (0.2, 0.5, 1.0)]
    assert recalls == sorted(recalls)
    assert recalls[-1] == 1.0


def test_cross_validate_holds_out_whole_groups() -> None:
    """族単位 hold-out: 学習に使われた族の行が予測に含まれないこと。"""
    rng = np.random.default_rng(0)
    rows = []
    for family in ("f1", "f2", "f3"):
        for c in range(3):
            for t in range(12):
                signal = 1 if (t % 3 == 0) else 0
                rows.append(
                    ds.Row(
                        change_id=f"{family}-{c}",
                        family=family,
                        task_id=f"T-{t:03d}",
                        label_regressed=signal if rng.random() > 0.15 else 1 - signal,
                        label_failed=0,
                        cost=1.0,
                        static={
                            "x": float(t % 3),
                            "change_kind_prompt": 1.0,
                            "touches_change": 0.0,
                        },
                    )
                )
    result = model_mod.cross_validate(rows, "logistic", group_by="family")
    assert result.n_groups == 3
    assert len(result.predictions) == len(rows)
    assert result.roc_auc > 0.7, "学習可能な信号を入れたのに AUC が上がらない"


@pytest.mark.parametrize("name", ["prior", "coverage"])
def test_baseline_models_need_no_fitting(name: str) -> None:
    rows = [
        ds.Row(
            change_id="c1",
            family="f1",
            task_id=f"T-{i}",
            label_regressed=i % 2,
            label_failed=0,
            cost=1.0,
            static={"base_pass_rate": i / 10, "overlap_ratio": 1 - i / 10},
        )
        for i in range(10)
    ]
    result = model_mod.cross_validate(rows, name, group_by="family")  # type: ignore[arg-type]
    assert len(result.predictions) == len(rows)
