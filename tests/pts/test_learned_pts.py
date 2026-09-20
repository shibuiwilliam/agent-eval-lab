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
    fault = changes["syn_fault_calendar_search_always"]
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


def _row(change_id: str, seq: int, units: set[str], task_id: str, regressed: int) -> ds.Row:
    return ds.Row(
        change_id=change_id,
        family="prompt",
        task_id=task_id,
        seq=seq,
        units=set(units),
        label_regressed=regressed,
        label_failed=regressed,
        cost=1.0,
        static={"change_kind_prompt": 1.0, "touches_change": 1.0, "base_pass_rate": 0.8},
    )


def test_history_features_never_see_the_future() -> None:
    """ラグ・履歴特徴は `seq` より前の記録しか見ない（時系列の漏洩を構造的に防ぐ）。"""
    from agenteval.pts.history import ChangeRecord, History, TestRunRecord

    history = History()
    for seq, regressed in ((0, 1), (1, 0), (5, 1)):
        history.add_change(
            ChangeRecord(seq=seq, change_id=f"c{seq}", kind="prompt", family="prompt", units={"a"})
        )
        history.add_run(
            TestRunRecord(
                seq=seq,
                change_id=f"c{seq}",
                task_id="T-1",
                passed=not regressed,
                regressed=bool(regressed),
            )
        )
    # seq=2 の時点では seq=5 の記録は見えない
    at2 = history.features(2, "T-1", {"a"})
    assert at2["hist_run_count"] == 2.0
    assert at2["lag_since_test_regressed"] == 2.0  # 直近の回帰は seq=0
    assert at2["history_depth"] == 2.0
    # seq=6 では 3 件すべて見える
    at6 = history.features(6, "T-1", {"a"})
    assert at6["hist_run_count"] == 3.0
    assert at6["lag_since_test_regressed"] == 1.0  # 直近の回帰は seq=5


def test_lag_marks_a_test_that_has_never_run() -> None:
    from agenteval.pts.history import History

    history = History()
    features = history.features(7, "T-new", {"a"})
    assert features["never_run_before"] == 1.0
    assert features["hist_run_count"] == 0.0
    # 一度も走っていないテストのラグは「履歴の長さ」に潰す（0 にしない）
    assert features["lag_since_test_ran"] == 7.0


def test_unit_regress_rate_is_per_unit_not_per_test() -> None:
    """要素そのものの危うさ（この要素を触ると落ちやすいか）。"""
    from agenteval.pts.history import ChangeRecord, History, TestRunRecord

    history = History()
    history.add_change(
        ChangeRecord(seq=0, change_id="c0", kind="prompt", family="prompt", units={"risky"})
    )
    history.add_change(
        ChangeRecord(seq=1, change_id="c1", kind="prompt", family="prompt", units={"safe"})
    )
    for task, seq, reg in (("T-1", 0, True), ("T-2", 0, True), ("T-1", 1, False)):
        history.add_run(
            TestRunRecord(seq=seq, change_id=f"c{seq}", task_id=task, passed=not reg, regressed=reg)
        )
    assert history.unit_regress_rate("risky", 5) == 1.0
    assert history.unit_regress_rate("safe", 5) == 0.0
    assert history.unit_regress_rate("unseen", 5) == 0.0


def test_history_built_from_a_pool_excludes_rows_outside_it() -> None:
    """交差検証では訓練フォールドの行だけから台帳を組む（hold-out のラベルが漏れない）。"""
    rows = [_row("c0", 0, {"a"}, "T-1", 1), _row("c1", 1, {"a"}, "T-1", 1)]
    full = model_mod._history_of(rows)
    partial = model_mod._history_of(rows[:1])
    assert full.features(2, "T-1", {"a"})["hist_regress_count"] == 2.0
    assert partial.features(2, "T-1", {"a"})["hist_regress_count"] == 1.0


def test_churn_features_separate_config_from_prompt_changes() -> None:
    """変更量: 設定変更は行数 0、プロンプト変更は行数 > 0。"""
    changes = {c.id: c for c in synthetic.generate()}
    prompt = changes["syn_drop_verification"].churn
    config = changes["syn_maxsteps_3"].churn
    assert prompt["lines_changed"] > 0
    assert config["lines_changed"] == 0
    assert ds._churn_features(config)["churn_is_pure_config"] == 1.0
    assert ds._churn_features(prompt)["churn_is_pure_config"] == 0.0


def test_same_unit_is_changed_more_than_once() -> None:
    """ラグ特徴が定義できるためには、同じ要素が複数回変更される必要がある。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for change in synthetic.generate():
        counts.update(change.components)
    repeated = [u for u, n in counts.items() if n > 1]
    assert len(repeated) >= 10, f"再変更される要素が少なすぎる: {counts.most_common(5)}"


def test_changes_have_a_total_order() -> None:
    seqs = [c.seq for c in synthetic.generate()]
    assert sorted(seqs) == list(range(len(seqs))), "seq が 0..n-1 の全順序になっていない"


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
                        seq=len(rows),
                        units={family},
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
            seq=0,
            units={"u"},
            label_regressed=i % 2,
            label_failed=0,
            cost=1.0,
            static={"base_pass_rate": i / 10, "overlap_ratio": 1 - i / 10},
        )
        for i in range(10)
    ]
    result = model_mod.cross_validate(rows, name, group_by="family")  # type: ignore[arg-type]
    assert len(result.predictions) == len(rows)
