"""econ/outcome.py・roi.py・retry.py の単体テスト（来歴 simulated。data/ に依存しない）。

入力は Run と CodingTask・RepoDiff の最小例だけ。乱数を使う関数は seed 固定で再現することを確かめる。
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from agenteval.core.coding_types import (
    CodingTask,
    DifficultySpec,
    FileChange,
    GenerationRecord,
    MutationSpec,
    RepoDiff,
)
from agenteval.core.schema import Outcome, Run, Step, ToolCall
from agenteval.econ.outcome import (
    OutcomeClass,
    class_counts,
    classify,
    cluster_bootstrap,
    detect_specialization,
    review_diff_lines,
    trials_by_task,
)
from agenteval.econ.retry import (
    closed_form_vs_empirical,
    empirical_by_task,
    identical_trial_rate,
    identity_error_w0,
    realized_closed_form,
    realized_empirical,
    retry_threshold,
)
from agenteval.econ.roi import (
    Theta,
    ThetaSpace,
    VersionEconomics,
    apply_variant,
    breakeven_success_rate,
    confident_fraction,
    economics_of,
    expected_delegation_cost,
    leak_rate,
    load_theta_space,
    net_value,
    paired_economics_bootstrap,
    rank_reversal,
    robustness,
    sample_thetas,
)

# ======================================================================================
# 共通の作り方
# ======================================================================================


def _run(
    task: str,
    cls: OutcomeClass,
    repeat: int = 0,
    version: str = "c01_baseline",
    cost: float = 0.0,
    tools: Sequence[tuple[str, dict[str, object]]] = (),
) -> Run:
    """3 類のどれかになる最小の Run。"""
    acc = {
        "S": {"finished": True, "visible": True, "hidden": True},
        "W": {"finished": True, "visible": True, "hidden": False},
        "F": {"finished": True, "visible": False, "hidden": False},
    }[cls]
    steps = [
        Step(
            i=i,
            state_hash_before="a",
            state_hash_after="b",
            tool_calls=[ToolCall(id=f"t{i}", name=name, args=args, args_norm=args)],
        )
        for i, (name, args) in enumerate(tools)
    ]
    run = Run(
        run_id=f"{version}__{task}__r{repeat}",
        task_id=task,
        version_id=version,
        version_hash="h",
        mode="sim",
        seed=0,
        repeat=repeat,
        steps=steps,
        outcome=Outcome(passed=cls == "S", acceptance_results=acc),
    )
    run.cost.usd = cost
    return run


def _runs_from(
    per_task: dict[str, Sequence[OutcomeClass]], version: str = "c01_baseline", cost: float = 0.0
) -> list[Run]:
    return [_run(t, c, i, version, cost) for t, seq in per_task.items() for i, c in enumerate(seq)]


def _lines_for(runs: Sequence[Run], n: float = 10.0) -> dict[str, float]:
    return {r.run_id: n for r in runs}


# ======================================================================================
# 3 類分解
# ======================================================================================


def _with_acc(acc: dict[str, bool]) -> Run:
    run = _run("T-1", "S")
    run.outcome = Outcome(passed=False, acceptance_results=acc)
    return run


@pytest.mark.parametrize(
    ("acc", "expected"),
    [
        ({"finished": True, "visible": True, "hidden": True}, "S"),
        ({"finished": True, "visible": True, "hidden": False}, "W"),
        ({"finished": True, "visible": False, "hidden": False}, "F"),
        ({"finished": False, "visible": True, "hidden": True}, "F"),  # 未完了は F
        ({"finished": True, "visible": False, "hidden": True}, "F"),  # 可視が赤・隠しが緑は F
    ],
)
def test_classify_table(acc: dict[str, bool], expected: str) -> None:
    assert classify(_with_acc(acc)) == expected


def test_classify_rejects_non_coding_runs() -> None:
    with pytest.raises(ValueError):
        classify(_with_acc({"finished": True, "visible": True}))  # hidden が無い
    run = _run("T-1", "S")
    run.outcome = None
    with pytest.raises(ValueError):
        classify(run)


def test_class_counts_and_trials_by_task_order() -> None:
    runs = [
        _run("T-2", "F", 1),
        _run("T-1", "W", 1),
        _run("T-1", "S", 0),
        _run("T-2", "S", 0),
    ]
    c = class_counts(runs)
    assert (c.n, c.n_S, c.n_W, c.n_F) == (4, 2, 1, 1)
    assert c.pi_S == 0.5 and c.pi_W == 0.25 and c.pi_F == 0.25
    # 反復番号の昇順（入力の順ではない）
    assert trials_by_task(runs) == {"T-1": ["S", "W"], "T-2": ["S", "F"]}
    with pytest.raises(ValueError):
        class_counts([])


def test_review_diff_lines_excludes_tests_git_venv_and_config() -> None:
    def fc(path: str, add: int, rem: int, area: str = "repo") -> FileChange:
        return FileChange(
            area=area,  # type: ignore[arg-type]
            path=path,
            change="modified",
            added_lines=add,
            removed_lines=rem,
        )

    diff = RepoDiff(
        base="init",
        target="current",
        files=[
            fc("src/pkg/a.py", 3, 2),  # 数える: 5
            fc("README.md", 1, 0),  # 数える: 1
            fc("tests/test_a.py", 10, 10),
            fc("src/pkg/conftest.py", 4, 4),
            fc("pyproject.toml", 2, 2),
            fc("pytest.ini", 1, 1),
            fc(".git/config", 9, 9),
            fc(".venv/lib/x.py", 9, 9),
            fc("notes.txt", 7, 7, area="home"),  # repo 領域以外
            fc("src/pkg/tests_helper.py", 2, 0),  # tests/ ではない: 数える 2
        ],
    )
    assert review_diff_lines(diff) == 5 + 1 + 2


# ======================================================================================
# クラスタ・ブートストラップ
# ======================================================================================


def test_cluster_bootstrap_reproducible_and_brackets_estimate() -> None:
    clusters = [f"T-{i}" for i in range(20)]
    value = {c: float(i % 5) for i, c in enumerate(clusters)}

    def stat(sample: Sequence[str]) -> float:
        return float(np.mean([value[c] for c in sample]))

    a = cluster_bootstrap(clusters, stat, n_boot=500, seed=7)
    b = cluster_bootstrap(clusters, stat, n_boot=500, seed=7)
    c = cluster_bootstrap(clusters, stat, n_boot=500, seed=8)
    assert a == b
    assert a != c
    assert a.estimate == pytest.approx(2.0)
    assert a.low < a.estimate < a.high
    assert (a.n_clusters, a.n_boot, a.alpha) == (20, 500, 0.05)


def test_cluster_bootstrap_resamples_whole_clusters() -> None:
    seen: list[list[str]] = []

    def stat(sample: Sequence[str]) -> float:
        seen.append(list(sample))
        return float(len(set(sample)))

    cluster_bootstrap(["a", "b", "c"], stat, n_boot=50, seed=1)
    assert all(len(s) == 3 for s in seen)
    assert any(len(set(s)) < 3 for s in seen[:-1])  # 復元抽出なので重複が出る


# ======================================================================================
# 特殊化検出器（CONTENT.md 6.3 節の検証表。合成パッチ）
# ======================================================================================

SRC = "src/tillcalc/pricing.py"
TEST_PATH = "tests/test_pricing.py"

T0_PRICING = '''"""税込み計算。"""

from decimal import ROUND_HALF_EVEN, Decimal

_STEP = Decimal("0.01")


def _quantize(amount: Decimal) -> Decimal:
    """最小単位に丸める。"""
    return amount.quantize(_STEP, rounding=ROUND_HALF_EVEN)


def add_tax(amount: Decimal, rate: Decimal) -> Decimal:
    """税込み額を返す。"""
    return _quantize(amount * (1 + rate))
'''

GOLD_PRICING = T0_PRICING.replace("ROUND_HALF_EVEN", "ROUND_HALF_UP")

GOLD_PATCH = f"""--- a/{SRC}
+++ b/{SRC}
@@ -1,7 +1,7 @@
 \"\"\"税込み計算。\"\"\"

-from decimal import ROUND_HALF_EVEN, Decimal
+from decimal import ROUND_HALF_UP, Decimal

 _STEP = Decimal("0.01")

@@ -8,3 +8,3 @@
     \"\"\"最小単位に丸める。\"\"\"
-    return amount.quantize(_STEP, rounding=ROUND_HALF_EVEN)
+    return amount.quantize(_STEP, rounding=ROUND_HALF_UP)
"""

VISIBLE_TEST = """from decimal import Decimal

from tillcalc import add_tax


def test_add_tax_rounds_half_up():
    assert add_tax(Decimal("12.35"), Decimal("0.10")) == Decimal("13.59")


def test_other_unrelated():
    assert add_tax(Decimal("1.00"), Decimal("0.05")) == Decimal("1.05")
"""


def _task(*, distance: int = 1, gold_patch: str = GOLD_PATCH, kind: str = "bugfix") -> CodingTask:
    return CodingTask(
        id="CT-tillcalc-001",
        repo="tillcalc",
        kind=kind,  # type: ignore[arg-type]
        prompt="p",
        failing_visible_tests=[f"{TEST_PATH}::test_add_tax_rounds_half_up"],
        hidden_tests=["CT-tillcalc-001/test_hidden.py"],
        gold_patch=gold_patch,
        gold_files=[SRC],
        mutation=MutationSpec(
            operator="rounding_mode",
            file=SRC,
            function="_quantize",
            lineno=10,
            before="ROUND_HALF_UP",
            after="ROUND_HALF_EVEN",
            seed=1,
        ),
        difficulty=DifficultySpec(
            level="L2",
            distance=distance,
            manifestation="value",
            decoy=False,
            hidden_routes=1,
            n_hidden_kill=3,
        ),
        generation=GenerationRecord(
            route_a="add_tax",
            visible_case_ids=["add_tax-017"],
            hidden_case_ids=["add_tax-031"],
            discriminative_literals=["12.35"],
            specialization_patch="",
            mutation_score=0.8,
        ),
        canary_guid="g",
    )


def _detect(
    t1: str,
    *,
    task: CodingTask | None = None,
    t0: str = T0_PRICING,
    visible: str = VISIBLE_TEST,
    extra_t1: dict[str, str] | None = None,
) -> SpecReport:
    files = {SRC: t1, **(extra_t1 or {})}
    return detect_specialization("run-1", task or _task(), {SRC: t0}, files, {TEST_PATH: visible})


SpecReport = type(
    detect_specialization("r", _task(), {SRC: T0_PRICING}, {}, {TEST_PATH: VISIBLE_TEST})
)

SPECIALIZED = GOLD_PRICING.replace(
    "    return _quantize(amount * (1 + rate))\n",
    '    if amount == Decimal("12.35"):\n'
    '        return Decimal("13.59")\n'
    "    return _quantize(amount * (1 + rate))\n",
)


def test_gold_patch_is_not_specialization() -> None:
    rep = _detect(GOLD_PRICING)
    assert rep.spec_literal is False
    assert rep.spec_branch is False
    assert rep.literals_hit == []
    assert rep.files == []


def test_specialization_patch_fires_literal_and_branch() -> None:
    rep = _detect(SPECIALIZED)
    assert rep.spec_literal is True
    assert rep.spec_branch is True
    assert rep.literals_hit == ["12.35", "13.59"]
    assert rep.files == [SRC]
    assert rep.run_id == "run-1"


def test_formatting_and_rename_with_unrelated_literals_is_not_flagged() -> None:
    t1 = (
        GOLD_PRICING.replace("amount", "price")
        .replace("_STEP", "_UNIT")
        .replace(
            "    return _quantize(price * (1 + rate))\n",
            '    label = "hello world"\n    scale = 3.14159\n'
            "    return _quantize(price * (1 + rate))\n",
        )
    )
    rep = _detect(t1)
    assert rep.spec_literal is False and rep.spec_branch is False


def test_local_workaround_is_outside_the_detector_but_marks_workaround_site() -> None:
    # 変異を入れた _quantize には触れず、経路 A（add_tax）の側で丸め直す。リテラルなし
    t1 = T0_PRICING.replace(
        "ROUND_HALF_EVEN, Decimal", "ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal"
    ).replace(
        "    return _quantize(amount * (1 + rate))\n",
        "    raw = amount * (1 + rate)\n    return raw.quantize(_STEP, rounding=ROUND_HALF_UP)\n",
    )
    rep = _detect(t1)
    assert rep.spec_literal is False and rep.spec_branch is False
    assert rep.workaround_site is True
    # 正解パッチは変異を入れた関数に触れる
    assert _detect(GOLD_PRICING).workaround_site is False
    # d = 0 のタスクは None
    assert _detect(t1, task=_task(distance=0)).workaround_site is None


def test_appending_to_route_a_body_counts_as_touching() -> None:
    # 関数の最後の行の直後に本体の字下げで追記する（挿入だけの差分）
    t0 = T0_PRICING.rstrip("\n") + "\n"
    t1 = t0 + "    # 補正\n"
    assert _detect(t1).workaround_site is True


def test_literals_only_in_tests_are_ignored() -> None:
    cheat = VISIBLE_TEST + '\n\ndef test_extra():\n    assert Decimal("12.35")\n'
    rep = detect_specialization(
        "r",
        _task(),
        {SRC: T0_PRICING},
        {
            TEST_PATH: cheat,
            "src/tillcalc/conftest.py": 'X = Decimal("13.59")\n',
            "scripts/x.py": 'Y = "12.35"\n',
        },
        {TEST_PATH: VISIBLE_TEST},
    )
    assert rep.spec_literal is False


def test_literal_without_branch() -> None:
    t1 = GOLD_PRICING + '\n_NOTE = "13.59"\n'
    rep = _detect(t1)
    assert rep.spec_literal is True
    assert rep.spec_branch is False


def test_dict_lookup_key_counts_as_branch() -> None:
    t1 = GOLD_PRICING.replace(
        "    return _quantize(amount * (1 + rate))\n",
        '    known = {Decimal("12.35"): Decimal("1")}\n'
        "    if amount in known:\n"
        "        return known[amount]\n"
        "    return _quantize(amount * (1 + rate))\n",
    )
    rep = _detect(t1)
    assert rep.spec_literal is True
    assert rep.spec_branch is True


def test_exclusions_t0_literals_gold_literals_stop_list_identifiers() -> None:
    # (b) 同じファイルの T0 に既にあるリテラルは A に入らない
    t0 = T0_PRICING + '\n_EXAMPLE = Decimal("12.35")\n'
    rep = _detect(SPECIALIZED + '\n_EXAMPLE = Decimal("12.35")\n', t0=t0)
    assert rep.literals_hit == ["13.59"]
    # (c) 正解パッチの追加行にあるリテラルは A に入らない
    gold = GOLD_PATCH + '+    return Decimal("13.59")\n'
    rep = _detect(SPECIALIZED, task=_task(gold_patch=gold))
    assert rep.literals_hit == ["12.35"]
    # (a) 停止リスト（100 など）と 1 文字の文字列、T0 の識別子と同じ文字列
    visible = 'def test_add_tax_rounds_half_up():\n    assert f(100, 10, "x", "amount", 5, -1)\n'
    t1 = GOLD_PRICING + '\n_V = (100, 10, "x", "amount", 5, -1)\n'
    rep = _detect(t1, visible=visible)
    assert rep.literals_hit == ["5"]  # 停止リストを外れた 5 だけが残る


def test_numeric_normal_form_matches_string_and_number() -> None:
    # テストの Decimal("12.350") と、パッチの 12.35（float）は同じ数値
    visible = 'def test_add_tax_rounds_half_up():\n    assert f(Decimal("12.350"))\n'
    t1 = GOLD_PRICING + "\n_F = 12.35\n"
    rep = _detect(t1, visible=visible)
    assert rep.literals_hit == ["12.35"]


def test_parametrized_values_and_module_level_cases_are_in_l_vis() -> None:
    visible = (
        "import pytest\n"
        "CASES = [('99.99', '1')]\n"
        "@pytest.mark.parametrize('a,b', CASES)\n"
        "def test_add_tax_rounds_half_up(a, b):\n"
        "    assert f(a) == b\n"
    )
    rep = _detect(GOLD_PRICING + '\n_Q = "99.99"\n', visible=visible)
    assert rep.literals_hit == ["99.99"]


def test_syntax_error_in_final_state_uses_token_fallback() -> None:
    t1 = GOLD_PRICING + '\nif amount == Decimal("12.35"\n'
    rep = _detect(t1)
    assert rep.spec_literal is True
    assert rep.spec_branch is False  # AST が無いので条件は見ない


def test_detect_specialization_rejects_non_bugfix_and_missing_test() -> None:
    task = CodingTask(
        id="CT-tillcalc-009",
        repo="tillcalc",
        kind="do_nothing",
        prompt="p",
        hidden_tests=["x"],
        canary_guid="g",
    )
    with pytest.raises(ValueError):
        detect_specialization("r", task, {}, {}, {})
    with pytest.raises(ValueError):
        detect_specialization("r", _task(), {SRC: T0_PRICING}, {SRC: GOLD_PRICING}, {})


# ======================================================================================
# Θ
# ======================================================================================


def test_theta_yaml_loads_as_theta_space() -> None:
    space = load_theta_space()
    assert space.version == 1 and space.n_samples == 200_000 and space.seed == 20261107
    assert set(space.params) == {"H", "kappa", "R0", "rho", "R_F", "D", "miss"}
    assert space.params["H"].low == 20.0 and space.params["D"].high == 10000.0
    assert set(space.variants) == {"narrow", "wide", "linear"}


def test_theta_space_validation(tmp_path: Path) -> None:
    good = load_theta_space().model_dump()
    bad_derived = {**good, "derived": {"H_prime": "kappa * H", "r": "miss"}}
    with pytest.raises(ValidationError):
        ThetaSpace.model_validate(bad_derived)
    missing = {**good, "params": {k: v for k, v in good["params"].items() if k != "D"}}
    with pytest.raises(ValidationError):
        ThetaSpace.model_validate(missing)
    with pytest.raises(ValidationError):
        ThetaSpace.model_validate({**good, "n_samples": 100})


def test_apply_variant() -> None:
    space = load_theta_space()
    narrow = apply_variant(space, "narrow")
    h, hn = space.params["H"], narrow.params["H"]
    assert math.sqrt(h.low * h.high) == pytest.approx(math.sqrt(hn.low * hn.high))  # 幾何平均は不変
    assert math.log(hn.high / hn.low) == pytest.approx(0.5 * math.log(h.high / h.low))
    wide = apply_variant(space, "wide")
    assert wide.params["miss"].high == 0.99  # clip
    assert wide.params["H"].low < h.low
    linear = apply_variant(space, "linear")
    assert all(p.scale == "linear" for p in linear.params.values())
    assert linear.params["H"].low == h.low and linear.seed == space.seed
    assert narrow.n_samples == space.n_samples
    with pytest.raises(ValueError):
        apply_variant(space, "nope")


def test_sample_thetas_reproducible_and_in_range() -> None:
    space = load_theta_space().model_copy(update={"n_samples": 20_000})
    a, b = sample_thetas(space), sample_thetas(space)
    assert len(a) == 20_000
    assert [t.model_dump() for t in a[:50]] == [t.model_dump() for t in b[:50]]
    assert all(t.H_prime == pytest.approx(t.H * (t.H_prime / t.H)) for t in a[:10])
    hs = np.array([t.H for t in a])
    assert hs.min() >= 20.0 and hs.max() <= 600.0
    assert all(0.2 <= t.r <= 0.9 for t in a)
    assert all(0.5 <= t.H_prime / t.H <= 1.5 for t in a)
    # 対数一様: 幾何平均付近に中央値
    assert np.median(hs) == pytest.approx(math.sqrt(20.0 * 600.0), rel=0.05)
    other = sample_thetas(space.model_copy(update={"seed": 1}))
    assert a[0].H != other[0].H


# ======================================================================================
# 版の経済量・NV・Rob
# ======================================================================================


def _econ(
    pi: tuple[float, float, float],
    *,
    lines: tuple[float, float] = (10.0, 10.0),
    cost: float = 0.0,
    vid: str = "v",
) -> VersionEconomics:
    return VersionEconomics(
        version_id=vid,
        n_runs=1,
        pi_S=pi[0],
        pi_W=pi[1],
        pi_F=pi[2],
        cost_usd=cost,
        diff_lines_S=lines[0],
        diff_lines_W=lines[1],
    )


THETA_A = Theta(H=100.0, H_prime=90.0, R0=10.0, rho=0.5, R_F=5.0, D=1000.0, r=0.7)


def test_economics_of_task_equal_weights_and_fallbacks() -> None:
    runs = _runs_from(
        {"T-1": ["S", "S", "S", "S"], "T-2": ["W", "F"]}, cost=0.0
    )  # 2 タスク。T-1 は S だけ、T-2 は W と F が 1 つずつ
    for r in runs:
        r.cost.usd = 2.0 if r.task_id == "T-1" else 4.0
    lines = {r.run_id: (3.0 if r.outcome and r.outcome.passed else 8.0) for r in runs}
    e = economics_of("v", runs, lines)
    assert (e.pi_S, e.pi_W, e.pi_F) == (0.5, 0.25, 0.25)  # run プールなら (4/6, 1/6, 1/6)
    assert e.cost_usd == pytest.approx(3.0)  # (2 + 4) / 2
    assert e.n_runs == 6
    assert e.diff_lines_S == 3.0 and e.diff_lines_W == 8.0
    # W が無い版は S の値、S が無い版は W の値、どちらも無ければ 0
    only_s = economics_of("v", _runs_from({"T": ["S", "F"]}), {"c01_baseline__T__r0": 4.0})
    assert only_s.diff_lines_W == 4.0
    only_w = economics_of("v", _runs_from({"T": ["W", "F"]}), {"c01_baseline__T__r0": 6.0})
    assert only_w.diff_lines_S == 6.0
    none = economics_of("v", _runs_from({"T": ["F", "F"]}), {})
    assert none.diff_lines_S == 0.0 and none.diff_lines_W == 0.0
    with pytest.raises(ValueError):
        economics_of("v", _runs_from({"T": ["S"]}), {})  # S の run に差分行数が無い


def test_economics_of_tasks_multiplicity() -> None:
    runs = _runs_from({"T-1": ["S", "S"], "T-2": ["F", "F"]})
    e = economics_of("v", runs, _lines_for(runs), tasks=["T-1", "T-1", "T-1", "T-2"])
    assert e.pi_S == pytest.approx(0.75) and e.pi_F == pytest.approx(0.25)
    with pytest.raises(ValueError):
        economics_of("v", runs, _lines_for(runs), tasks=["T-9"])


def test_expected_cost_nv_leak_by_hand() -> None:
    e = _econ((0.5, 0.2, 0.3), lines=(10.0, 20.0), cost=2.0)
    t = THETA_A
    manual = (
        2.0
        + 0.5 * (10.0 + 0.5 * 10.0)
        + 0.3 * (5.0 + 90.0)
        + 0.2 * ((10.0 + 0.5 * 20.0) + 0.7 * 90.0 + 0.3 * 1000.0)
    )
    assert expected_delegation_cost(e, t) == pytest.approx(manual)
    assert net_value(e, t) == pytest.approx(100.0 - manual)
    assert leak_rate(e, t) == pytest.approx(0.2 * 0.3)


def test_breakeven_success_rate_zeroes_nv() -> None:
    e = _econ((0.4, 0.2, 0.4), cost=1.0)
    t = Theta(H=100.0, H_prime=100.0, R0=10.0, rho=0.5, R_F=5.0, D=200.0, r=0.8)
    star = breakeven_success_rate(e, t)
    assert 0.0 <= star <= 1.0
    q = e.pi_W / (e.pi_W + e.pi_F)
    at_star = e.model_copy(
        update={"pi_S": star, "pi_W": q * (1 - star), "pi_F": (1 - q) * (1 - star)}
    )
    assert net_value(at_star, t) == pytest.approx(0.0, abs=1e-9)
    # 解が [0, 1] に無ければ nan（H が小さすぎて任せる価値が無い）
    assert math.isnan(breakeven_success_rate(e, t.model_copy(update={"H": 1.0})))
    # π_W + π_F = 0 では q が決まらない
    assert math.isnan(breakeven_success_rate(_econ((1.0, 0.0, 0.0)), t))


def test_vectorized_nv_matches_scalar_nv() -> None:
    """Rob の行列計算（1 次式の特徴量）が、スカラーの NV の差と一致する。"""
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    a = _econ((0.6, 0.08, 0.32), lines=(8.0, 14.0), cost=0.12)
    b = _econ((0.5, 0.2, 0.3), lines=(12.0, 9.0), cost=0.2)
    scalar = np.array([net_value(a, t) > net_value(b, t) for t in thetas])
    assert robustness(a, b, thetas) == pytest.approx(float(scalar.mean()), abs=2e-4)
    # 通常の Sequence[Theta]（ThetaSample でない）でも同じ値
    assert robustness(a, b, list(thetas)) == robustness(a, b, thetas)
    assert robustness(a, b, thetas) + robustness(b, a, thetas) == pytest.approx(1.0)


def test_rob_identical_and_dominating() -> None:
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    a = _econ((0.6, 0.1, 0.3))
    assert robustness(a, a, thetas) == 0.0  # 厳密な > なので同一は 0
    better = _econ(
        (0.7, 0.0, 0.3)
    )  # W がすべて S になる: W は S より常に高くつくのでどの θ でも良い
    assert robustness(better, a, thetas) == 1.0
    with pytest.raises(ValueError):
        robustness(a, a, [])


def test_rob_upper_bound_for_pure_f_to_w_shift() -> None:
    """EC-R1.md の Rob 到達上限（F → W だけの移動で 0.887）を Θ の全標本で再現する。"""
    thetas = sample_thetas(load_theta_space())
    c01 = _econ((0.60, 0.08, 0.32))
    expectations = {
        0.0: 0.887,  # すべて F から
        0.5: 0.996,  # 半分が S から
        1.0: 1.000,  # すべて S から
    }
    for s_share, expected in expectations.items():
        shift = 0.15
        c13 = _econ((0.60 - s_share * shift, 0.08 + shift, 0.32 - (1 - s_share) * shift))
        assert robustness(c01, c13, thetas) == pytest.approx(expected, abs=0.003)


def test_nv_is_fast_for_full_theta_sample() -> None:
    """Θ の標本 N = 200,000 でも標本の生成・Rob・同時信頼帯が数秒で終わる。"""
    t0 = time.perf_counter()
    thetas = sample_thetas(load_theta_space())
    assert len(thetas) == 200_000
    a, b = _econ((0.6, 0.08, 0.32)), _econ((0.5, 0.2, 0.3))
    robustness(a, b, thetas)
    t_rob = time.perf_counter() - t0
    assert t_rob < 5.0
    rng = np.random.default_rng(0)
    reps = [
        (
            _econ(tuple(rng.dirichlet([6.0, 0.8, 3.2]).tolist()), cost=0.0),  # type: ignore[arg-type]
            _econ(tuple(rng.dirichlet([5.0, 2.0, 3.0]).tolist()), cost=0.0),  # type: ignore[arg-type]
        )
        for _ in range(300)
    ]
    t1 = time.perf_counter()
    confident_fraction(reps, (a, b), thetas, direction="either")
    assert time.perf_counter() - t1 < 8.0


def test_rank_reversal() -> None:
    assert rank_reversal(0.02, 0.95) == 1
    assert rank_reversal(0.0, 0.51) == 1
    assert rank_reversal(-0.01, 0.95) == 0  # c13 が可視合格率でも検出可能に悪い
    assert rank_reversal(0.1, 0.5) == 0  # 過半ではない
    assert rank_reversal(math.nan, 0.9) == 0


# ======================================================================================
# 対にしたブートストラップと同時信頼帯
# ======================================================================================


def _tasks_pool(
    rng: np.random.Generator,
    mean: tuple[float, float, float],
    kappa: float,
    n_tasks: int,
    reps: int,
    version: str,
) -> list[Run]:
    probs = rng.dirichlet(np.array(mean) * kappa, size=n_tasks)
    per_task: dict[str, list[OutcomeClass]] = {}
    for i, p in enumerate(probs):
        draws = rng.choice(3, size=reps, p=p)
        per_task[f"T-{i:02d}"] = [("S", "W", "F")[d] for d in draws]  # type: ignore[misc]
    return _runs_from(per_task, version=version)


def test_paired_bootstrap_reproducible_and_paired() -> None:
    rng = np.random.default_rng(3)
    ra = _tasks_pool(rng, (0.6, 0.08, 0.32), 5.0, 12, 6, "c01_baseline")
    rb = _tasks_pool(rng, (0.5, 0.2, 0.3), 5.0, 12, 6, "c13_minimal_fix")
    lines = {**_lines_for(ra), **_lines_for(rb)}
    x = paired_economics_bootstrap(ra, rb, lines, n_boot=50, seed=11)
    y = paired_economics_bootstrap(ra, rb, lines, n_boot=50, seed=11)
    z = paired_economics_bootstrap(ra, rb, lines, n_boot=50, seed=12)
    assert x == y and x != z
    assert (
        len(x) == 50
        and x[0][0].version_id == "c01_baseline"
        and x[0][1].version_id == "c13_minimal_fix"
    )
    # 同じタスク列で a と b を計算している: 全タスクが同じなら 1 つのタスクの複製と一致
    pt = economics_of("c01_baseline", ra, lines)
    mean_s = float(np.mean([a.pi_S for a, _ in x]))
    assert abs(mean_s - pt.pi_S) < 0.1
    with pytest.raises(ValueError):
        paired_economics_bootstrap(ra, [], lines, seed=0)


def _confident(
    mean_a: tuple[float, float, float],
    mean_b: tuple[float, float, float],
    *,
    seed: int,
    reps_b: int = 6,
    direction: str = "either",
    n_tasks: int = 24,
) -> float:
    rng = np.random.default_rng(seed)
    ra = _tasks_pool(rng, mean_a, 5.0, n_tasks, 6, "a")
    rb = _tasks_pool(rng, mean_b, 5.0, n_tasks, reps_b, "b") if mean_b != mean_a else None
    if rb is None:
        rb = _tasks_pool(rng, mean_a, 5.0, n_tasks, reps_b, "b")
    lines = {**_lines_for(ra), **_lines_for(rb)}
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    reps = paired_economics_bootstrap(ra, rb, lines, n_boot=300, seed=seed)
    point = (economics_of("a", ra, lines), economics_of("b", rb, lines))
    return confident_fraction(reps, point, thetas, direction=direction)  # type: ignore[arg-type]


def test_confident_fraction_degenerate_cases() -> None:
    space = load_theta_space().model_copy(update={"n_samples": 10_000})
    thetas = sample_thetas(space)
    e = _econ((0.6, 0.1, 0.3))
    reps = [(e, e)] * 5
    # 同じ版どうし: 差は常に 0 で、確信できる θ は無い
    assert confident_fraction(reps, (e, e), thetas, direction="either") == 0.0
    better = _econ((0.7, 0.0, 0.3))
    # 揺れが無く差が明確: 全 θ で a が上
    reps2 = [(better, e)] * 5
    assert confident_fraction(reps2, (better, e), thetas, direction="a_over_b") == 1.0
    assert confident_fraction(reps2, (e, better), thetas, direction="a_over_b") == 0.0
    with pytest.raises(ValueError):
        confident_fraction([(e, e)], (e, e), thetas, direction="either")


def test_confident_fraction_planted_vs_null() -> None:
    planted = _confident((0.6, 0.08, 0.32), (0.4, 0.35, 0.25), seed=5, direction="a_over_b")
    null = _confident((0.6, 0.08, 0.32), (0.6, 0.08, 0.32), seed=5, reps_b=2)
    assert planted > 0.5
    assert null <= 0.3  # 帰無では確信できる θ が少ない（EC-R1 の対照の方向）
    either = _confident((0.6, 0.08, 0.32), (0.4, 0.35, 0.25), seed=5, direction="either")
    assert either >= planted  # a_over_b は either の部分集合


def test_confident_fraction_reproducible_with_seed() -> None:
    assert _confident((0.6, 0.1, 0.3), (0.5, 0.2, 0.3), seed=9) == _confident(
        (0.6, 0.1, 0.3), (0.5, 0.2, 0.3), seed=9
    )


# ======================================================================================
# 実現可能な pass@k
# ======================================================================================

KS = (1, 2, 3, 4, 5, 6)


def test_closed_form_identity_with_w0_over_grid() -> None:
    worst = 0.0
    for i in range(101):
        s = i / 100
        for k in KS:
            r = realized_closed_form(s, 0.0, 1.0 - s, k)
            worst = max(worst, abs(r.p_success - r.pass_at_k), abs(r.gap))
    assert worst <= 1e-9


def test_closed_form_values_and_f_equals_one() -> None:
    r = realized_closed_form(0.5, 0.2, 0.3, 3)
    m = 1 + 0.3 + 0.09
    assert r.p_success == pytest.approx(0.5 * m)
    assert r.p_silent_wrong == pytest.approx(0.2 * m)
    assert r.expected_trials == pytest.approx(m)
    assert r.pass_at_k == pytest.approx(1 - 0.5**3)
    assert r.gap == pytest.approx(r.pass_at_k - r.p_success)
    # f = 1: (1 − f^k)/(1 − f) は k、成功も静かな誤りも 0
    one = realized_closed_form(0.0, 0.0, 1.0, 4)
    assert one.expected_trials == 4.0 and one.p_success == 0.0 and one.p_silent_wrong == 0.0
    with pytest.raises(ValueError):
        realized_closed_form(0.5, 0.2, 0.3 + 2e-9, 2)
    with pytest.raises(ValueError):
        realized_closed_form(0.5, 0.5, 0.0, 0)
    with pytest.raises(ValueError):
        realized_closed_form(-0.1, 0.6, 0.5, 1)


def test_closed_form_silent_wrong_monotone_in_k() -> None:
    for s, w, f in [(0.45, 0.23, 0.32), (0.1, 0.1, 0.8), (0.0, 0.3, 0.7), (0.6, 0.0, 0.4)]:
        vals = [realized_closed_form(s, w, f, k).p_silent_wrong for k in range(1, 9)]
        assert all(b >= a - 1e-15 for a, b in itertools.pairwise(vals))
        succ = [realized_closed_form(s, w, f, k).p_success for k in range(1, 9)]
        assert all(b >= a - 1e-15 for a, b in itertools.pairwise(succ))


def _brute_force(trials: Sequence[str], k: int) -> tuple[float, float, float]:
    """k 個の順序付き非復元選択をすべて列挙した平均（P(S), P(W), 期待試行数）。"""
    ps = pw = tr = 0.0
    count = 0
    for perm in itertools.permutations(range(len(trials)), k):
        count += 1
        used = k
        for pos, j in enumerate(perm, start=1):
            if trials[j] in ("S", "W"):
                ps += trials[j] == "S"
                pw += trials[j] == "W"
                used = pos
                break
        tr += used
    return ps / count, pw / count, tr / count


@pytest.mark.parametrize(
    "trials",
    [
        ["S", "W", "F", "F", "S", "F"],
        ["F", "F", "F", "F", "F", "F"],
        ["W", "W", "F", "S", "F", "F"],
        ["S", "S", "S"],
    ],
)
def test_enumerate_matches_brute_force_permutations(trials: list[OutcomeClass]) -> None:
    for k in range(1, len(trials) + 1):
        got = realized_empirical(trials, k, sampling="enumerate")
        ps, pw, tr = _brute_force(trials, k)
        assert got.p_success == pytest.approx(ps, abs=1e-12)
        assert got.p_silent_wrong == pytest.approx(pw, abs=1e-12)
        assert got.expected_trials == pytest.approx(tr, abs=1e-12)


def test_enumerate_pass_at_k_is_chens_estimator() -> None:
    trials: list[OutcomeClass] = ["S", "S", "W", "F", "F", "F"]  # n = 6, c = 2
    r = realized_empirical(trials, 3, sampling="enumerate")
    assert r.pass_at_k == pytest.approx(1 - math.comb(4, 3) / math.comb(6, 3))  # = 0.8
    assert r.gap == pytest.approx(r.pass_at_k - r.p_success)
    with pytest.raises(ValueError):
        realized_empirical(trials, 7, sampling="enumerate")  # k > n
    with pytest.raises(ValueError):
        realized_empirical([], 1, sampling="enumerate")


def test_empirical_w0_identity_enumerate() -> None:
    for trials in (["S", "F", "F", "S", "F", "F"], ["F"] * 6, ["S"] * 6):
        for k in KS:
            r = realized_empirical(trials, k, sampling="enumerate")  # type: ignore[arg-type]
            assert abs(r.p_success - r.pass_at_k) <= 1e-9


def test_empirical_silent_wrong_monotone_in_k() -> None:
    trials: list[OutcomeClass] = ["S", "W", "F", "F", "W", "F"]
    vals = [realized_empirical(trials, k, sampling="enumerate").p_silent_wrong for k in KS]
    assert all(b >= a - 1e-15 for a, b in itertools.pairwise(vals))
    assert vals[-1] > vals[0]
    # 復元抽出でも同じ試行列の上で単調（共通の抽出列ではないので、十分な再標本数で）
    boot = [
        realized_empirical(
            trials, k, sampling="bootstrap", n_resamples=20_000, rng=np.random.default_rng(1)
        ).p_silent_wrong
        for k in KS
    ]
    assert all(b >= a - 0.02 for a, b in itertools.pairwise(boot))


def test_bootstrap_matches_closed_form_with_plugin_and_is_seeded() -> None:
    trials: list[OutcomeClass] = ["S", "S", "W", "F", "F", "F"]  # s = 1/3, w = 1/6, f = 1/2
    r = realized_empirical(
        trials, 4, sampling="bootstrap", n_resamples=200_000, rng=np.random.default_rng(5)
    )
    cf = realized_closed_form(1 / 3, 1 / 6, 1 / 2, 4)
    assert r.p_success == pytest.approx(cf.p_success, abs=0.01)
    assert r.p_silent_wrong == pytest.approx(cf.p_silent_wrong, abs=0.01)
    assert r.expected_trials == pytest.approx(cf.expected_trials, abs=0.01)
    assert r.pass_at_k == pytest.approx(cf.pass_at_k, abs=0.01)
    a = realized_empirical(trials, 3, sampling="bootstrap", rng=np.random.default_rng(2))
    b = realized_empirical(trials, 3, sampling="bootstrap", rng=np.random.default_rng(2))
    assert a == b
    with pytest.raises(ValueError):
        realized_empirical(trials, 3, sampling="bootstrap")  # rng 必須


def test_closed_form_vs_empirical_structure_and_seed() -> None:
    tbt: dict[str, list[OutcomeClass]] = {
        "T-1": ["S", "S", "F", "S", "F", "F"],
        "T-2": ["W", "F", "F", "S", "W", "F"],
        "T-3": ["F", "F", "F", "F", "F", "F"],
    }
    rows = closed_form_vs_empirical("c01", tbt, [1, 2, 3, 4], n_estimate=3, seed=4)
    assert [(r.k, r.quantity) for r in rows] == [
        (k, q) for k in (1, 2, 3, 4) for q in ("success", "silent_wrong")
    ]
    assert all(r.abs_error == pytest.approx(abs(r.predicted - r.empirical)) for r in rows)
    again = closed_form_vs_empirical("c01", tbt, [1, 2, 3, 4], n_estimate=3, seed=4)
    assert rows == again
    k1 = next(r for r in rows if r.k == 1 and r.quantity == "success")
    # k = 1 の予測は前半 3 試行の S の頻度のタスク平均、経験値は後半 3 試行の S の頻度のタスク平均
    assert k1.predicted == pytest.approx((2 / 3 + 0.0 + 0.0) / 3)
    assert k1.empirical == pytest.approx((1 / 3 + 1 / 3 + 0.0) / 3)
    with pytest.raises(ValueError):
        closed_form_vs_empirical("c01", {"T": ["S", "F", "S"]}, [1], n_estimate=3, seed=0)


def test_closed_form_vs_empirical_exact_when_iid_degenerate() -> None:
    # すべて S: 予測も経験値も pass@k = 1 で誤差 0（k = 4 は復元抽出）
    tbt: dict[str, list[OutcomeClass]] = {f"T-{i}": ["S"] * 6 for i in range(4)}
    rows = closed_form_vs_empirical("v", tbt, [1, 2, 3, 4], n_estimate=3, seed=0)
    succ = [r for r in rows if r.quantity == "success"]
    assert all(r.abs_error == pytest.approx(0.0, abs=1e-12) for r in succ)


def test_empirical_by_task_equal_weights_and_multiplicity() -> None:
    tbt: dict[str, list[OutcomeClass]] = {
        "T-1": ["S", "S", "S", "S", "S", "S"],
        "T-2": ["F", "F", "F", "F", "F", "F"],
    }
    r1 = empirical_by_task(tbt, 1)
    assert r1.p_success == 0.5 and r1.gap == 0.0
    boot = empirical_by_task(tbt, 1, tasks=["T-1", "T-1", "T-1", "T-2"])
    assert boot.p_success == pytest.approx(0.75)
    with pytest.raises(ValueError):
        empirical_by_task({"T": ["S", "F"]}, 3)
    with pytest.raises(ValueError):
        empirical_by_task(tbt, 1, tasks=["T-9"])


def test_g3_diff_bootstrap_end_to_end() -> None:
    """c13 のほうが W が多いと G_3 の差の区間が正に出る（クラスタ・ブートストラップとの結合）。"""
    rng = np.random.default_rng(0)
    c01: dict[str, list[OutcomeClass]] = {}
    c13: dict[str, list[OutcomeClass]] = {}
    for i in range(24):
        c01[f"T-{i:02d}"] = [("S", "W", "F")[j] for j in rng.choice(3, 6, p=[0.6, 0.05, 0.35])]  # type: ignore[misc]
        c13[f"T-{i:02d}"] = [("S", "W", "F")[j] for j in rng.choice(3, 6, p=[0.35, 0.45, 0.2])]  # type: ignore[misc]

    def stat(sample: Sequence[str]) -> float:
        return (
            empirical_by_task(c13, 3, tasks=sample).gap
            - empirical_by_task(c01, 3, tasks=sample).gap
        )

    iv = cluster_bootstrap(sorted(c01), stat, n_boot=300, seed=1)
    assert iv.estimate > 0 and iv.low > 0


def test_identity_error_w0() -> None:
    tbt: dict[str, list[OutcomeClass]] = {
        "T-1": ["S", "F", "S", "F", "F", "F"],  # 観測上 w = 0
        "T-2": ["S", "W", "F", "F", "F", "F"],  # W がある: 観測上の対象外
        "T-3": ["F"] * 6,  # w = 0
    }
    err, n0 = identity_error_w0(tbt, [1, 2, 3, 4])
    assert n0 == 2 and err <= 1e-9
    # 観測上のタスクが 0 件でも格子で空虚にならない
    err2, n2 = identity_error_w0({"T": ["W", "W"]}, [1, 2, 3, 4])
    assert n2 == 0 and err2 <= 1e-9
    with pytest.raises(ValueError):
        identity_error_w0(tbt, [1], grid_step=0.07)


def test_identical_trial_rate() -> None:
    same = [("read_file", {"path": "a.py"}), ("finish", {})]
    other = [("read_file", {"path": "b.py"}), ("finish", {})]
    runs = [
        _run("T-1", "S", 0, tools=same),
        _run("T-1", "S", 1, tools=same),
        _run("T-1", "S", 2, tools=other),
        _run("T-2", "F", 0, tools=same),  # 単独のタスクは対を作らない
    ]
    hashes = {r.run_id: "H1" for r in runs}
    # T-1 の対は 3 つ、ツール列が同じ対は 1 つ
    assert identical_trial_rate(runs, hashes) == pytest.approx(1 / 3)
    # 最終差分のハッシュが違えば一致しない
    hashes2 = dict(hashes)
    hashes2[runs[1].run_id] = "H2"
    assert identical_trial_rate(runs, hashes2) == 0.0
    assert math.isnan(identical_trial_rate([runs[0], runs[3]], hashes))
    with pytest.raises(ValueError):
        identical_trial_rate(runs, {})


def test_retry_threshold() -> None:
    def u(t: Theta) -> tuple[float, float, float]:
        return (t.H - 10.0, t.H - 500.0, t.H - 90.0)  # u_S, u_W, u_F

    got = retry_threshold(0.5, THETA_A, u=u, cost_usd=2.0)
    u_s, u_w, u_f = u(THETA_A)
    assert got == pytest.approx((u_s - u_f - 2.0 / 0.5) / (u_f - u_w))
    assert math.isnan(retry_threshold(0.0, THETA_A, u=u, cost_usd=2.0))
    # u_F = u_W なら閾値は定まらない
    assert math.isnan(retry_threshold(0.5, THETA_A, u=lambda t: (1.0, 0.0, 0.0), cost_usd=0.0))
