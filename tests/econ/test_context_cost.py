"""econ/context_cost.py の単体テスト（課金の割り振り・台帳との対応づけ・履歴キャッシュの予測）。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agenteval.core.schema import Run, Step, ToolCall, ToolResult, Usage
from agenteval.econ.context_cost import (
    AllocationPlant,
    UnitPrices,
    allocate,
    cca_of_step,
    cca_of_tool,
    cca_ratio,
    counterfactual_cost,
    history_cache_prediction_error,
    match_ledger,
    perturb_drop_tool_result,
    plant_eligible,
    plant_relative_effect,
    predicted_resend_ratio,
    verbose_attribution,
)
from agenteval.llm.cost import REPO_ROOT, LedgerEntry, load_pricing

HAIKU = "claude-haiku-4-5-20251001"
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "traces"
PRICES = UnitPrices(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1)
MTOK = 1_000_000.0


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _step(
    i: int,
    inp: int,
    out: int,
    cw: int = 0,
    cr: int = 0,
    calls: Sequence[tuple[str, str, str]] = (),
    text: str = "",
) -> Step:
    """calls は (id, ツール名, 結果の本文) の列。"""
    return Step(
        i=i,
        state_hash_before="a",
        state_hash_after="b",
        assistant_text=text,
        tool_calls=[ToolCall(id=c[0], name=c[1], args={}, args_norm={}) for c in calls],
        tool_results=[ToolResult(id=c[0], content=c[2]) for c in calls],
        usage=Usage(
            input_tokens=inp,
            output_tokens=out,
            cache_creation_input_tokens=cw,
            cache_read_input_tokens=cr,
        ),
    )


def _run(steps: list[Step], run_id: str = "r") -> Run:
    return Run(
        run_id=run_id,
        task_id="T",
        version_id="v",
        version_hash="h",
        mode="live",
        seed=0,
        steps=steps,
    )


def _hand_run() -> Run:
    """手計算できる 3 ステップの run（履歴キャッシュ無し。接頭辞だけがキャッシュ対象）。"""
    return _run(
        [
            _step(0, inp=100, out=50, cw=1000),
            _step(1, inp=300, out=20, cr=1000),
            _step(2, inp=450, out=10, cr=1000),
        ]
    )


def _blocks_run(
    outs: Sequence[int],
    taus: Sequence[int],
    *,
    prefix: int = 1000,
    user0: int = 100,
    history_cache: bool = False,
    tools: Sequence[str] | None = None,
    run_id: str = "r",
) -> Run:
    """ブロック長（出力・ツール結果）から、位置モデルどおりの usage を持つ run を作る。

    outs は n 個、taus は n−1 個（最後のステップのツール結果は再送されない）。
    history_cache=True: 初回リクエストは全体を書き込み、以降は前リクエスト分を読み出して増分を書き込む。
    """
    n = len(outs)
    steps: list[Step] = []
    total = prefix + user0
    for m in range(n):
        if m > 0:
            total += outs[m - 1] + taus[m - 1]
        if history_cache:
            if m == 0:
                u = {"inp": 0, "cw": total, "cr": 0}
            else:
                prev_total = total - outs[m - 1] - taus[m - 1]
                u = {"inp": 0, "cw": total - prev_total, "cr": prev_total}
        elif m == 0:
            u = {"inp": user0, "cw": prefix, "cr": 0}
        else:
            u = {"inp": total - prefix, "cw": 0, "cr": prefix}
        calls = [(f"c{m}", tools[m], "x")] if tools else []
        steps.append(_step(m, out=outs[m], calls=calls, **u))
    return _run(steps, run_id)


# ---------------------------------------------------------------------------
# 単価
# ---------------------------------------------------------------------------


def test_unit_prices_from_pricing_reads_yaml() -> None:
    prices = UnitPrices.from_pricing(load_pricing(REPO_ROOT / "pricing.yaml"), HAIKU)
    assert (prices.input, prices.output, prices.cache_write, prices.cache_read) == (
        1.0,
        5.0,
        1.25,
        0.1,
    )


def test_unit_prices_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        UnitPrices.from_pricing(load_pricing(REPO_ROOT / "pricing.yaml"), "no-such-model")


# ---------------------------------------------------------------------------
# 割り振り（手計算）
# ---------------------------------------------------------------------------


def test_allocate_hand_computed() -> None:
    a = allocate(_hand_run(), PRICES)
    # 手計算（単価×トークン数、最後に 1e6 で割る）
    assert a.increments == [200, 150]
    assert [s.tokens for s in a.segments if s.seg_id in ("t0", "t1")] == [150, 130]
    assert a.prefix_usd == pytest.approx((1000 * 1.25 + 1000 * 0.1 * 2) / MTOK)
    assert a.initial_user_usd == pytest.approx(3 * 100 * 1.0 / MTOK)
    assert a.assistant_resend_usd == pytest.approx((50 + 50 + 20) / MTOK)
    assert a.cca_usd[0] == pytest.approx(2 * 150 / MTOK)
    assert a.cca_usd[1] == pytest.approx(130 / MTOK)
    assert a.output_usd == pytest.approx(80 * 5.0 / MTOK)
    assert a.direct_usd == pytest.approx(2700 / MTOK)
    assert a.allocated_usd == pytest.approx(2700 / MTOK)
    assert a.allocation_identity_error < 1e-12
    assert a.negative_increment_steps == []
    assert a.negative_share_steps == []


def test_allocate_single_step_and_empty() -> None:
    one = allocate(_run([_step(0, inp=10, out=5, cw=100)]), PRICES)
    assert one.n_steps == 1
    assert one.cca_usd == {}
    assert one.allocation_identity_error < 1e-12
    empty = allocate(_run([]), PRICES)
    assert empty.n_steps == 0 and empty.allocated_usd == 0.0


def test_allocate_keeps_negative_increment_and_identity() -> None:
    """総入力が減るステップがあっても切り捨てず、恒等式は保たれる。"""
    run = _run(
        [
            _step(0, inp=100, out=50, cw=1000),
            _step(1, inp=500, out=20, cr=1000),  # T1 = 1500
            _step(2, inp=200, out=10, cr=1000),  # T2 = 1200: Δ = −300
        ]
    )
    a = allocate(run, PRICES)
    assert a.increments == [400, -300]
    assert a.negative_increment_steps == [2]
    assert a.negative_share_steps == [1]  # τ1 = −300 − 20
    assert a.allocation_identity_error < 1e-12


def test_m4_negative_share_counted_without_truncation() -> None:
    """τ_k < 0（出力が増分より大きい）でも件数に数え、課金は負のまま保持する。"""
    run = _run(
        [
            _step(0, inp=100, out=500, cw=1000),
            _step(1, inp=200, out=10, cr=1000),  # Δ1 = 100, τ0 = −400
        ]
    )
    a = allocate(run, PRICES)
    assert a.negative_share_steps == [0]
    assert a.negative_increment_steps == []
    assert a.cca_usd[0] < 0
    assert a.allocation_identity_error < 1e-12


_usage = st.tuples(
    st.integers(0, 3000),  # input
    st.integers(0, 800),  # output
    st.integers(0, 3000),  # cache_creation
    st.integers(0, 3000),  # cache_read
)


@given(st.lists(_usage, min_size=1, max_size=8))
def test_allocation_identity_holds_for_any_usage(rows: list[tuple[int, int, int, int]]) -> None:
    """どんな usage の列でも（総入力が減る列も）、成分の和は直接計算に戻る（望遠鏡和）。"""
    run = _run([_step(i, inp=r[0], out=r[1], cw=r[2], cr=r[3]) for i, r in enumerate(rows)])
    a = allocate(run, PRICES)
    assert a.allocation_identity_error < 1e-9
    assert a.negative_increment_steps == [m for m in range(1, len(rows)) if a.increments[m - 1] < 0]


def test_history_cache_usage_allocates_to_write_then_read() -> None:
    """履歴キャッシュ有りの usage では、t<k> は初回が書き込み、以降が読み出しになる。"""
    run = _blocks_run([50, 40, 30, 20], [100, 200, 300], history_cache=True)
    a = allocate(run, PRICES)
    assert a.allocation_identity_error < 1e-12
    # t0（100 トークン）はリクエスト 1 で書き込み、2・3 で読み出し
    assert a.cca_usd[0] == pytest.approx(100 * (1.25 + 0.1 * 2) / MTOK)
    # t1（200）はリクエスト 2 で書き込み、3 で読み出し
    assert a.cca_usd[1] == pytest.approx(200 * (1.25 + 0.1) / MTOK)
    assert a.cca_usd[2] == pytest.approx(300 * 1.25 / MTOK)


def test_history_cache_off_ccas_follow_formula() -> None:
    """off なら CCA_i = τ_i · p_in · n_i（NEW_IDEA 6 章の表）。"""
    run = _blocks_run([50, 40, 30, 20], [100, 200, 300])
    a = allocate(run, PRICES)
    for k, tau in enumerate([100, 200, 300]):
        n_i = 3 - k  # 以降のリクエスト数
        assert a.cca_usd[k] == pytest.approx(tau * 1.0 * n_i / MTOK)


# ---------------------------------------------------------------------------
# ツールへの按分
# ---------------------------------------------------------------------------


def test_cca_split_by_result_chars_and_names() -> None:
    run = _run(
        [
            _step(
                0,
                inp=100,
                out=50,
                cw=1000,
                calls=[("a", "read", "x" * 30), ("b", "grep", "y" * 10)],
            ),
            _step(1, inp=400, out=10, cr=1000, calls=[("c", "read", "z")]),
        ]
    )
    a = allocate(run, PRICES)
    assert a.cca_usd[0] == pytest.approx((400 - 100 - 50) / MTOK)  # τ0 = 250 を 1 回再送
    assert cca_of_tool(a, "read") == pytest.approx(0.75 * a.cca_usd[0])
    assert cca_of_tool(a, "grep") == pytest.approx(0.25 * a.cca_usd[0])
    assert cca_of_tool(a, "absent") == 0.0
    assert sum(a.cca_by_tool.values()) == pytest.approx(sum(a.cca_usd.values()))
    assert a.segments[3].tool_names == ["read", "grep"]  # t0


def test_cca_by_tool_same_name_twice_and_no_tool_step() -> None:
    run = _run(
        [
            _step(0, inp=100, out=50, cw=1000, calls=[("a", "t", "aa"), ("b", "t", "bb")]),
            _step(1, inp=400, out=10, cr=1000),  # 呼び出し無し
            _step(2, inp=500, out=10, cr=1000),
        ]
    )
    a = allocate(run, PRICES)
    assert cca_of_tool(a, "t") == pytest.approx(a.cca_usd[0])
    assert cca_of_tool(a, "<no_tool>") == pytest.approx(a.cca_usd[1])
    assert sum(a.cca_by_tool.values()) == pytest.approx(sum(a.cca_usd.values()))


def test_char_ratio_tool_tokens_is_recorded() -> None:
    run = _run(
        [
            _step(0, inp=100, out=50, cw=1000, calls=[("a", "t", "x" * 100)], text=""),
            _step(1, inp=400, out=10, cr=1000),
        ]
    )
    a = allocate(run, PRICES)
    # Δ1 = 300、アシスタント側の文字数は名前 1 ＋ 引数 "{}" 2 = 3、ツール結果 100 文字
    assert a.char_ratio_tool_tokens[0] == pytest.approx(300 * 100 / 103)


def test_cca_of_step_bounds() -> None:
    a = allocate(_hand_run(), PRICES)
    assert cca_of_step(a, 0) == a.cca_usd[0]
    assert cca_of_step(a, 2) == 0.0  # 最後のステップの出力は再送されない
    with pytest.raises(IndexError):
        cca_of_step(a, 3)


# ---------------------------------------------------------------------------
# 植込み
# ---------------------------------------------------------------------------


def test_plant_p2_drops_assistant_resend() -> None:
    a = allocate(_hand_run(), PRICES, plant=AllocationPlant(drop_assistant_resend=True))
    assert a.assistant_resend_usd == 0.0
    assert a.direct_usd - a.allocated_usd == pytest.approx(120 / MTOK)
    assert a.allocation_identity_error == pytest.approx(120 / 2700)
    assert plant_relative_effect(_hand_run(), PRICES, "P2") == pytest.approx(120 / 2700)


def test_plant_p3_prices_cache_write_as_input() -> None:
    a = allocate(_hand_run(), PRICES, plant=AllocationPlant(cache_write_as_input=True))
    assert a.direct_usd - a.allocated_usd == pytest.approx(1000 * 0.25 / MTOK)
    assert plant_relative_effect(_hand_run(), PRICES, "P3") == pytest.approx(250 / 2700)


def test_plant_eligibility_by_planted_size() -> None:
    assert plant_eligible(_hand_run(), PRICES, "P2")
    # 再送が無い 1 ステップの run では P2 の影響は 0 なので適格でない
    one = _run([_step(0, inp=10, out=5, cw=100)])
    assert not plant_eligible(one, PRICES, "P2")
    assert plant_eligible(one, PRICES, "P3")  # 書き込みがあるので P3 の影響は大きい


def _p4_run() -> Run:
    return _run(
        [
            _step(0, inp=100, out=50, cw=1000),
            _step(1, inp=700, out=20, cr=1000),  # T1 = 1700、τ0 = 550
            _step(2, inp=800, out=10, cr=1000),  # T2 = 1800、Δ = 100
        ]
    )


def test_perturb_drop_tool_result_creates_negative_increment() -> None:
    run = _p4_run()
    before = run.model_dump()
    p = perturb_drop_tool_result(run, 0)
    assert run.model_dump() == before  # 元は変えない
    assert p.steps[-1].usage.input_tokens == 800 - 550
    a = allocate(p, PRICES)
    assert a.negative_increment_steps == [2]
    assert a.allocation_identity_error < 1e-12  # 植込み後も割り振り自体は壊れない
    assert plant_eligible(run, PRICES, "P4", j=0)
    assert plant_relative_effect(run, PRICES, "P4", j=0) == pytest.approx(550 / 3450)


def test_perturb_drop_tool_result_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        perturb_drop_tool_result(_p4_run(), 1)  # 最後のリクエストの直前の結果は対象外
    with pytest.raises(ValueError):
        perturb_drop_tool_result(_run([_step(0, 1, 1, cw=10), _step(1, 5, 1, cr=10)]), 0)
    assert not plant_eligible(_hand_run(), PRICES, "P4", j=0)  # τ0 = 150 は Δ = 150 を上回らない


# ---------------------------------------------------------------------------
# 反実仮想・履歴キャッシュの予測
# ---------------------------------------------------------------------------


def test_predicted_resend_ratio_matches_new_idea_example() -> None:
    assert predicted_resend_ratio(10, PRICES) == pytest.approx(0.215)
    assert predicted_resend_ratio(1, PRICES) == pytest.approx(
        1.25
    )  # 1 回だけなら書き込みの分だけ高い
    with pytest.raises(ValueError):
        predicted_resend_ratio(0, PRICES)


def test_predicted_ratio_uses_pricing_yaml_values() -> None:
    prices = UnitPrices.from_pricing(load_pricing(REPO_ROOT / "pricing.yaml"), HAIKU)
    assert math.isclose(predicted_resend_ratio(10, prices), 0.215, abs_tol=0.005)


def test_counterfactual_off_equals_direct_for_off_usage() -> None:
    run = _hand_run()
    assert counterfactual_cost(run, PRICES, history_cache=False) == pytest.approx(2700 / MTOK)


def test_counterfactual_on_equals_direct_for_on_usage() -> None:
    """位置モデルどおりにキャッシュが効いた usage では、on の反実仮想は直接計算に一致する。"""
    run = _blocks_run([50, 40, 30, 20, 10], [100, 200, 300, 50], history_cache=True)
    direct = allocate(run, PRICES).direct_usd
    assert counterfactual_cost(run, PRICES, history_cache=True) == pytest.approx(direct)
    assert counterfactual_cost(run, PRICES, history_cache=False) > direct


def test_counterfactual_saving_matches_closed_form() -> None:
    """節約額 = Σ ブロック長 × [p_in·n − (p_w + p_r(n−1))]（再送回数 n）。"""
    outs, taus = [50, 40, 30, 20], [100, 200, 300]
    run = _blocks_run(outs, taus, user0=0)
    saving = counterfactual_cost(run, PRICES, history_cache=False) - counterfactual_cost(
        run, PRICES, history_cache=True
    )
    expected = 0.0
    for k in range(3):
        n_k = 3 - k
        expected += (outs[k] + taus[k]) * (1.0 * n_k - (1.25 + 0.1 * (n_k - 1)))
    assert saving == pytest.approx(expected / MTOK)


def _on_off_runs() -> tuple[list[Run], list[Run], dict[str, float]]:
    ledger: dict[str, float] = {}
    on_runs = [
        _blocks_run([50, 40, 30, 20], [100, 200, 300], history_cache=True, run_id="on1"),
        _blocks_run([60, 30, 20], [400, 100], history_cache=True, run_id="on2"),
    ]
    off_runs = [
        _blocks_run([50, 40, 30, 20], [100, 200, 300], run_id="off1"),
        _blocks_run([60, 30, 20], [400, 100], run_id="off2"),
    ]
    for r in (*on_runs, *off_runs):
        ledger[r.run_id] = allocate(r, PRICES).direct_usd
    return on_runs, off_runs, ledger


def test_cache_prediction_error_zero_when_ledger_follows_model() -> None:
    on_runs, off_runs, ledger = _on_off_runs()
    r = history_cache_prediction_error(on_runs, off_runs, ledger, PRICES)
    assert r.e_on == pytest.approx(0.0, abs=1e-12)
    assert r.e_off == pytest.approx(0.0, abs=1e-12)
    assert r.error == pytest.approx(0.0, abs=1e-12)
    assert (r.n_on, r.n_off) == (2, 2)


def test_cache_prediction_error_detects_cache_miss_in_on_runs() -> None:
    """on の run の実費がモデルより高い（キャッシュの取りこぼし）と e_on が正になり error に出る。"""
    on_runs, off_runs, ledger = _on_off_runs()
    ledger["on1"] *= 1.5
    r = history_cache_prediction_error(on_runs, off_runs, ledger, PRICES)
    assert r.e_on > 0.0
    assert r.e_off == pytest.approx(0.0, abs=1e-12)
    assert r.error == pytest.approx(abs(r.e_off - r.e_on))


def test_cache_prediction_error_old_definition_equals_abs_e_on() -> None:
    """改訂前の定義 |Σ S − Σ Ŝ| / Σ Ŝ（on の run だけ）は |e_on| に等しい。"""
    on_runs, off_runs, ledger = _on_off_runs()
    ledger["on1"] *= 1.3
    ledger["on2"] *= 0.9
    s_meas = 0.0
    s_pred = 0.0
    for r in on_runs:
        c_off = counterfactual_cost(r, PRICES, history_cache=False)
        c_on = counterfactual_cost(r, PRICES, history_cache=True)
        s_pred += c_off - c_on
        s_meas += c_off - ledger[r.run_id]
    old = abs(s_meas - s_pred) / s_pred
    result = history_cache_prediction_error(on_runs, off_runs, ledger, PRICES)
    assert old == pytest.approx(abs(result.e_on))


def test_cache_prediction_error_off_only_residual() -> None:
    on_runs, off_runs, ledger = _on_off_runs()
    ledger["off2"] *= 1.2
    r = history_cache_prediction_error(on_runs, off_runs, ledger, PRICES)
    assert r.e_on == pytest.approx(0.0, abs=1e-12)
    assert r.e_off > 0.0
    assert r.error == pytest.approx(r.e_off)


def test_cache_prediction_error_invalid_inputs() -> None:
    on_runs, off_runs, ledger = _on_off_runs()
    with pytest.raises(ValueError):
        history_cache_prediction_error([], off_runs, ledger, PRICES)
    with pytest.raises(ValueError):
        history_cache_prediction_error(on_runs, [], ledger, PRICES)
    with pytest.raises(KeyError):
        history_cache_prediction_error(on_runs, off_runs, {}, PRICES)
    no_saving = [_run([_step(0, inp=10, out=5, cw=100)], "n1")]  # 1 ステップは節約が 0
    with pytest.raises(ValueError):
        history_cache_prediction_error(no_saving, off_runs, {**ledger, "n1": 0.0}, PRICES)


# ---------------------------------------------------------------------------
# 帰属の比・対照
# ---------------------------------------------------------------------------


def _tools_run(extra0: int, extra1: int, run_id: str) -> Run:
    """4 ステップ。ステップ 0 が run_tests、ステップ 1 が file_read（結果の大きさを変えられる）。"""
    return _blocks_run(
        [10, 10, 10, 10],
        [100 + extra0, 100 + extra1, 100],
        tools=["run_tests", "file_read", "file_read", "finish"],
        run_id=run_id,
    )


def test_verbose_attribution_hand_computed_ratio() -> None:
    """run_tests（再送 3 回）と file_read（再送 2 回）が同じ量だけ増えたとき、帰属は 3/5。"""
    x = 400
    base = {t: [allocate(_tools_run(0, 0, f"b{t}{r}"), PRICES) for r in range(2)] for t in "ABC"}
    treated = {t: [allocate(_tools_run(x, x, f"t{t}"), PRICES)] for t in "ABC"}
    est = verbose_attribution(base, treated, n_boot=200, seed=1)
    assert est.value == pytest.approx(3 / 5)
    assert est.ci_low == pytest.approx(3 / 5) and est.ci_high == pytest.approx(3 / 5)
    assert est.n_clusters == 3


def test_verbose_attribution_is_seeded_and_interval_contains_value() -> None:
    base = {t: [allocate(_tools_run(0, 0, f"b{t}"), PRICES)] for t in "ABCDE"}
    treated = {
        t: [allocate(_tools_run(100 * (i + 1), 50 * (5 - i), f"t{t}"), PRICES)]
        for i, t in enumerate("ABCDE")
    }
    a = verbose_attribution(base, treated, n_boot=500, seed=7)
    b = verbose_attribution(base, treated, n_boot=500, seed=7)
    assert a == b
    assert a.ci_low <= a.value <= a.ci_high


def test_verbose_attribution_requires_common_tasks() -> None:
    alloc = allocate(_tools_run(0, 0, "x"), PRICES)
    with pytest.raises(ValueError):
        verbose_attribution({"A": [alloc]}, {"B": [alloc]}, seed=0)


def test_cca_ratio_reflects_number_of_resends() -> None:
    """同じ大きさのツール結果を早いステップと遅いステップで出す。帰属比は再送回数の比 1/11。"""
    taus_early = [100] * 12
    taus_early[1] = 5000
    taus_late = [100] * 12
    taus_late[11] = 5000
    outs = [10] * 13
    early = allocate(_blocks_run(outs, taus_early), PRICES)
    late = allocate(_blocks_run(outs, taus_late), PRICES)
    ratio = cca_ratio(late, 11, early, 1)
    assert ratio == pytest.approx(1 / 11, rel=0.02)  # τ には出力の分の差し引きがあるが同じ大きさ
    assert ratio < 0.1
    with pytest.raises(ValueError):
        cca_ratio(late, 11, early, 12)  # 最後のステップの CCA は 0


# ---------------------------------------------------------------------------
# 台帳との対応づけ
# ---------------------------------------------------------------------------


def _entry(run_id: str, step: Step, usd: float, mode: str = "live") -> LedgerEntry:
    u = step.usage
    return LedgerEntry(
        ts="2026-01-01T00:00:00+00:00",
        run_id=run_id,
        step=step.i,
        model=HAIKU,
        mode=mode,
        usd=usd,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_input_tokens=u.cache_read_input_tokens,
        cache_creation_input_tokens=u.cache_creation_input_tokens,
    )


def test_match_ledger_exact_key_duplicates_and_rerun_rows() -> None:
    run = _hand_run()
    rows = [_entry("r", s, 1.0 + s.i) for s in run.steps]
    rows.append(_entry("r", run.steps[1], 99.0))  # 同一内容の重複行（ファイル順で後ろ）
    rerun = _step(2, inp=451, out=10, cr=1000)  # 再実行の行（トークン数が違う）
    rows.append(_entry("r", rerun, 7.0))
    rows.append(_entry("other", run.steps[0], 5.0))  # 別 run の行は無関係
    rep = match_ledger([run], rows)
    m = rep.runs["r"]
    assert m.fully_matched and m.matched_steps == [0, 1, 2]
    assert m.ledger_usd == pytest.approx(1.0 + 2.0 + 3.0)  # 重複は最初の行だけ
    assert m.duplicate_identical_rows == 1
    assert m.unmatched_ledger_rows == 1
    assert m.naive_usd == pytest.approx(1 + 2 + 3 + 99 + 7)
    assert rep.ledger_match_rate == 1.0 and rep.step_match_rate == 1.0
    assert rep.ledger_rows_considered == 5


def test_match_ledger_partial_and_modes() -> None:
    run = _hand_run()
    rows = [_entry("r", run.steps[0], 1.0), _entry("r", run.steps[1], 2.0, mode="sim")]
    rep = match_ledger([run], rows)
    m = rep.runs["r"]
    assert not m.fully_matched
    assert m.matched_steps == [0] and m.unmatched_steps == [1, 2]
    assert rep.ledger_match_rate == 0.0
    assert rep.step_match_rate == pytest.approx(1 / 3)
    rep_sim = match_ledger([run], rows, modes=("sim",))
    assert rep_sim.runs["r"].matched_steps == [1]


def test_match_ledger_requires_all_four_token_counts() -> None:
    run = _hand_run()
    bad = _entry("r", run.steps[0], 1.0).model_copy(update={"cache_creation_input_tokens": 999})
    rep = match_ledger([run], [bad])
    assert rep.runs["r"].matched_steps == [] and rep.runs["r"].unmatched_ledger_rows == 1


# ---------------------------------------------------------------------------
# 固定の 3 本（既存 live 軌跡のコピー）
# ---------------------------------------------------------------------------


def _fixture_runs() -> list[Run]:
    return [
        Run.model_validate_json(p.read_text("utf-8")) for p in sorted(FIXTURES.glob("live__*.json"))
    ]


def _fixture_ledger() -> list[LedgerEntry]:
    lines = (FIXTURES / "ledger_rows.jsonl").read_text("utf-8").splitlines()
    return [LedgerEntry.model_validate_json(x) for x in lines if x.strip()]


def _fixture_prices() -> UnitPrices:
    return UnitPrices.from_pricing(load_pricing(REPO_ROOT / "pricing.yaml"), HAIKU)


def test_fixture_runs_identity_against_ledger() -> None:
    runs = _fixture_runs()
    assert len(runs) == 3
    prices = _fixture_prices()
    rep = match_ledger(runs, _fixture_ledger())
    assert rep.ledger_match_rate == 1.0
    for r in runs:
        m = rep.runs[r.run_id]
        a = allocate(r, prices)
        assert m.fully_matched
        assert abs(a.allocated_usd - m.ledger_usd) / m.ledger_usd <= 0.001
        assert a.allocation_identity_error <= 1e-9
        assert a.negative_increment_steps == []


def test_fixture_includes_run_with_rerun_rows() -> None:
    """台帳の行数がステップ数より多い run（K3）を含み、素朴な照合は恒等式を破る。"""
    runs = _fixture_runs()
    prices = _fixture_prices()
    rep = match_ledger(runs, _fixture_ledger())
    extra = [m for m in rep.runs.values() if m.unmatched_ledger_rows > 0]
    assert extra
    by_id = {r.run_id: r for r in runs}
    for m in extra:
        a = allocate(by_id[m.run_id], prices)
        assert abs(a.allocated_usd - m.naive_usd) / m.naive_usd > 0.001
        assert abs(a.allocated_usd - m.ledger_usd) / m.ledger_usd <= 0.001


def test_fixture_plants_are_detected() -> None:
    runs = _fixture_runs()
    prices = _fixture_prices()
    rep = match_ledger(runs, _fixture_ledger())
    n_cases = 0
    for r in runs:
        m = rep.runs[r.run_id]
        if plant_eligible(r, prices, "P2"):
            n_cases += 1
            a = allocate(r, prices, plant=AllocationPlant(drop_assistant_resend=True))
            assert abs(a.allocated_usd - m.ledger_usd) / m.ledger_usd > 0.001
        if plant_eligible(r, prices, "P3"):
            n_cases += 1
            a = allocate(r, prices, plant=AllocationPlant(cache_write_as_input=True))
            assert abs(a.allocated_usd - m.ledger_usd) / m.ledger_usd > 0.001
    assert n_cases > 0
