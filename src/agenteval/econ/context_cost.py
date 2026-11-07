"""コンテキスト・コスト帰属（EC-B2P・EC-B2）— 課金の割り振りと台帳との対応づけ。

原典: docs/NEW_IDEA.md 6 章 EC-B2、docs/plan/coding/SPEC.md 9.1・10 節、TASK.md 0.4 節 M4。

考え方:
  リクエスト m のプロンプトは、先頭から `prefix`（system＋ツール定義）、`user0`（初回ユーザー
  メッセージ）、k = 0..m-1 について `a<k>`（アシスタント出力の再送）と `t<k>`（ツール結果）の順に並ぶ。
  `usage` の 3 つの値は連続区間の長さ（キャッシュ読み出し、キャッシュ書き込み、通常入力の順）なので、
  各ブロックの累積位置と照合すれば、そのブロックにどの単価が掛かったかが一意に決まる。
  ブロックの課金は累積価格関数 F_m の差で計算するため、総和は望遠鏡和で必ず F_m(T_m) に戻る。

重要な約束:
  - 単価は pricing.yaml の数値を UnitPrices に写して使う。`llm/cost.py` の `Pricing.usd()` は
    呼ばない（台帳との照合が同じ関数どうしの比較になるのを避ける。EC-B2P）。
  - ブロック長が負でも切り捨てない（切り捨てると分割が壊れ、負の増分を隠してしまう）。
  - このモジュールはファイルを読まない。台帳の読み込みは呼び出し側の責務。
  - ledger_identity_error が検証するのは、単価表の写し・台帳の行の対応づけ・総和であり、
    ブロックごとの割り振りの正しさ（どのブロックにいくら掛かったか）ではない。ブロック別の正しさは
    位置モデルの仮定（連続するリクエストの総入力は減らない）と植込み・対照の検査で別に確かめる。
  - history_cache: on の run は user0 の末尾にも区切りが付き、ステップ 0 の input_tokens がほぼ 0 になる。
    その run では T0 − in0 が接頭辞（system＋ツール定義）に user0 を含んでしまうので、
    接頭辞のトークン数 prefix_tokens を明示的に渡す（allocate・counterfactual_cost・H3 の予測誤差）。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from agenteval.core.schema import Run, Step
from agenteval.llm.cost import LedgerEntry, Pricing

_MTOK = 1_000_000.0
# 恒等式の許容値（EC-B2P の確定基準）。植込みの適格条件と検出判定に使う
LEDGER_IDENTITY_TOLERANCE = 0.001
# 植込みの適格条件: 許容値の 2 倍以上の影響がある run にだけ植え込む
PLANT_ELIGIBLE_EFFECT = 2 * LEDGER_IDENTITY_TOLERANCE
# ツール呼び出しの無いステップの CCA を帰属させるキー
NO_TOOL_KEY = "<no_tool>"


# ---------------------------------------------------------------------------
# 単価
# ---------------------------------------------------------------------------


class UnitPrices(BaseModel):
    """USD / MTok。"""

    input: float
    output: float
    cache_write: float
    cache_read: float

    @classmethod
    def from_pricing(cls, pricing: Pricing, model: str) -> UnitPrices:
        """pricing.models[model] の input / output / cache_write_5m / cache_read を写す。

        既定値で補わない（無い項目は KeyError）。黙って過小評価しないため。
        """
        p = pricing.models[model]
        return cls(
            input=p["input"],
            output=p["output"],
            cache_write=p["cache_write_5m"],
            cache_read=p["cache_read"],
        )


# ---------------------------------------------------------------------------
# 型
# ---------------------------------------------------------------------------

SegmentKind = Literal["prefix", "initial_user", "assistant", "tool_results"]


class Segment(BaseModel):
    seg_id: str  # "prefix" | "user0" | "a<k>" | "t<k>"
    kind: SegmentKind
    origin_step: int  # prefix / user0 は -1
    tokens: int  # 負になりうる
    tool_names: list[str] = Field(default_factory=list)  # t<k>: ステップ k の呼び出しのツール名


class Charge(BaseModel):
    seg_id: str
    request_step: int
    usd: float


class AllocationPlant(BaseModel):
    """EC-B2P の植込み（計測器の感度検査用）。既定はすべて False（正しい割り振り）。"""

    drop_assistant_resend: bool = False  # P2: a<k> の課金を落とす
    cache_write_as_input: bool = False  # P3: キャッシュ書き込みを通常入力の単価で計算する


class RunAllocation(BaseModel):
    run_id: str
    n_steps: int
    segments: list[Segment]
    charges: list[Charge]
    output_usd: float
    prefix_usd: float
    initial_user_usd: float
    assistant_resend_usd: float
    cca_usd: dict[int, float]  # k -> CCA_k（t<k> の全リクエストでの課金の和）
    cca_by_tool: dict[str, float]  # 複数呼び出しのステップは結果の文字数比で CCA_k を按分
    allocated_usd: float  # A_r = 成分の総和（出力を含む）
    direct_usd: float  # C_r = Σ_m usage_m × 単価（ステップごとに直接）
    allocation_identity_error: float  # |A_r − C_r| / C_r
    increments: list[int]  # Δ_m（m = 1..n−1）。補正前
    negative_increment_steps: list[int]  # Δ_m < 0 の m
    negative_share_steps: list[int]  # τ_k < 0 の k
    char_ratio_tool_tokens: dict[int, float]  # M4 の照合用: Δ_{k+1} × 文字数比のツール結果トークン


# ---------------------------------------------------------------------------
# 内部: 使用量・累積価格関数
# ---------------------------------------------------------------------------


def _total_input(step: Step) -> int:
    """リクエストの総入力 T = input + cache_creation + cache_read。"""
    u = step.usage
    return u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens


def _direct_step_usd(step: Step, prices: UnitPrices) -> float:
    """ステップ 1 回分の費用を usage × 単価で直接計算する（出力を含む）。"""
    u = step.usage
    return (
        u.input_tokens * prices.input
        + u.output_tokens * prices.output
        + u.cache_creation_input_tokens * prices.cache_write
        + u.cache_read_input_tokens * prices.cache_read
    ) / _MTOK


def _cum_price(
    step: Step, prices: UnitPrices, *, write_as_input: bool = False
) -> Callable[[float], float]:
    """リクエストの累積価格関数 F(x)（USD）。

    [0, cr) は読み出し、[cr, cr+cw) は書き込み、それ以降は通常入力の単価。
    x < 0 は読み出し単価で、書き込み区間の後ろは通常入力の単価で線形に延長する。
    延長は線形なので F(T) は入力が負の値でも usage の直接計算に一致する。
    """
    cr = step.usage.cache_read_input_tokens
    cw = step.usage.cache_creation_input_tokens
    p_r = prices.cache_read
    p_w = prices.input if write_as_input else prices.cache_write
    p_in = prices.input

    def f(x: float) -> float:
        if x <= 0:
            return x * p_r / _MTOK
        if x <= cr:
            return x * p_r / _MTOK
        if x <= cr + cw:
            return (cr * p_r + (x - cr) * p_w) / _MTOK
        return (cr * p_r + cw * p_w + (x - cr - cw) * p_in) / _MTOK

    return f


def _advance(f: Callable[[float], float], pos: float, length: float) -> tuple[float, float]:
    """位置 pos から length だけ進み、そのブロックの課金額と新しい位置を返す（length が負でも同じ式）。"""
    return f(pos + length) - f(pos), pos + length


def _assistant_chars(step: Step) -> int:
    """アシスタント出力の再送分の文字数の近似（本文＋ツール呼び出しの名前と引数）。"""
    n = len(step.assistant_text)
    for c in step.tool_calls:
        n += len(c.name) + len(json.dumps(c.args, ensure_ascii=False, sort_keys=True))
    return n


def _result_chars_by_call(step: Step) -> list[int]:
    """step.tool_calls の順に、対応する結果の文字数を返す（結果が無ければ 0）。"""
    by_id = {r.id: len(r.content) for r in step.tool_results}
    return [by_id.get(c.id, 0) for c in step.tool_calls]


def _split_cca(step: Step, cca_k: float) -> dict[str, float]:
    """CCA_k を、そのステップの呼び出しに結果の文字数比で按分する（ツール名ごとに合算）。

    呼び出しが無ければ NO_TOOL_KEY に全額。結果の文字数が全部 0 なら等分する。
    """
    names = [c.name for c in step.tool_calls]
    if not names:
        return {NO_TOOL_KEY: cca_k}
    chars = _result_chars_by_call(step)
    total = sum(chars)
    weights = [ch / total for ch in chars] if total > 0 else [1.0 / len(names)] * len(names)
    out: dict[str, float] = {}
    for name, w in zip(names, weights, strict=True):
        out[name] = out.get(name, 0.0) + cca_k * w
    return out


# ---------------------------------------------------------------------------
# 割り振り
# ---------------------------------------------------------------------------


def _prefix_and_user0(run: Run, prefix_tokens: int | None) -> tuple[int, int]:
    """(接頭辞のトークン数 P, 初回ユーザーメッセージのトークン数) を返す。

    prefix_tokens が None なら P = T0 − in0、user0 = in0（history_cache: off の run でだけ正しい。
    on の run は in0 がほぼ 0 なので user0 が P に入ってしまう）。
    与えられたときは P = prefix_tokens、user0 = T0 − P。
    """
    s0 = run.steps[0]
    t0 = _total_input(s0)
    if prefix_tokens is None:
        return t0 - s0.usage.input_tokens, s0.usage.input_tokens
    return prefix_tokens, t0 - prefix_tokens


def allocate(
    run: Run,
    prices: UnitPrices,
    *,
    plant: AllocationPlant | None = None,
    prefix_tokens: int | None = None,
) -> RunAllocation:
    """run の課金を、ブロック（prefix・user0・a<k>・t<k>）と出力に割り振る。

    prefix_tokens: system＋ツール定義だけのトークン数（history_cache: on の run で指定する）。
    """
    plant = plant or AllocationPlant()
    steps = run.steps
    n = len(steps)
    direct = sum(_direct_step_usd(s, prices) for s in steps)
    if n == 0:
        return RunAllocation(
            run_id=run.run_id,
            n_steps=0,
            segments=[],
            charges=[],
            output_usd=0.0,
            prefix_usd=0.0,
            initial_user_usd=0.0,
            assistant_resend_usd=0.0,
            cca_usd={},
            cca_by_tool={},
            allocated_usd=0.0,
            direct_usd=0.0,
            allocation_identity_error=0.0,
            increments=[],
            negative_increment_steps=[],
            negative_share_steps=[],
            char_ratio_tool_tokens={},
        )

    totals = [_total_input(s) for s in steps]
    outs = [s.usage.output_tokens for s in steps]
    increments = [totals[m] - totals[m - 1] for m in range(1, n)]  # increments[m-1] = Δ_m
    prefix_tokens, user0_tokens = _prefix_and_user0(run, prefix_tokens)
    # τ_k = Δ_{k+1} − out_k（k = 0..n−2）。切り捨てない
    taus = [increments[k] - outs[k] for k in range(n - 1)]

    segments: list[Segment] = [
        Segment(seg_id="prefix", kind="prefix", origin_step=-1, tokens=prefix_tokens),
        Segment(seg_id="user0", kind="initial_user", origin_step=-1, tokens=user0_tokens),
    ]
    for k in range(n - 1):
        segments.append(Segment(seg_id=f"a{k}", kind="assistant", origin_step=k, tokens=outs[k]))
        segments.append(
            Segment(
                seg_id=f"t{k}",
                kind="tool_results",
                origin_step=k,
                tokens=taus[k],
                tool_names=[c.name for c in steps[k].tool_calls],
            )
        )

    charges: list[Charge] = []
    prefix_usd = 0.0
    user0_usd = 0.0
    resend_usd = 0.0
    cca = {k: 0.0 for k in range(n - 1)}
    for m in range(n):
        f = _cum_price(steps[m], prices, write_as_input=plant.cache_write_as_input)
        pos = 0.0
        c, pos = _advance(f, pos, prefix_tokens)
        charges.append(Charge(seg_id="prefix", request_step=m, usd=c))
        prefix_usd += c
        c, pos = _advance(f, pos, user0_tokens)
        charges.append(Charge(seg_id="user0", request_step=m, usd=c))
        user0_usd += c
        for k in range(m):
            c, pos = _advance(f, pos, outs[k])
            if not plant.drop_assistant_resend:
                charges.append(Charge(seg_id=f"a{k}", request_step=m, usd=c))
                resend_usd += c
            c, pos = _advance(f, pos, taus[k])
            charges.append(Charge(seg_id=f"t{k}", request_step=m, usd=c))
            cca[k] += c

    output_usd = sum(outs[m] * prices.output for m in range(n)) / _MTOK
    allocated = output_usd + prefix_usd + user0_usd + resend_usd + sum(cca.values())

    cca_by_tool: dict[str, float] = {}
    for k in range(n - 1):
        for name, usd in _split_cca(steps[k], cca[k]).items():
            cca_by_tool[name] = cca_by_tool.get(name, 0.0) + usd

    char_ratio: dict[int, float] = {}
    for k in range(n - 1):
        a_chars = _assistant_chars(steps[k])
        t_chars = sum(_result_chars_by_call(steps[k]))
        denom = a_chars + t_chars
        char_ratio[k] = increments[k] * (t_chars / denom) if denom > 0 else 0.0

    if direct == 0.0:
        error = 0.0 if allocated == 0.0 else math.inf
    else:
        error = abs(allocated - direct) / direct

    return RunAllocation(
        run_id=run.run_id,
        n_steps=n,
        segments=segments,
        charges=charges,
        output_usd=output_usd,
        prefix_usd=prefix_usd,
        initial_user_usd=user0_usd,
        assistant_resend_usd=resend_usd,
        cca_usd=cca,
        cca_by_tool=cca_by_tool,
        allocated_usd=allocated,
        direct_usd=direct,
        allocation_identity_error=error,
        increments=increments,
        negative_increment_steps=[m for m in range(1, n) if increments[m - 1] < 0],
        negative_share_steps=[k for k in range(n - 1) if taus[k] < 0],
        char_ratio_tool_tokens=char_ratio,
    )


# ---------------------------------------------------------------------------
# 植込み（EC-B2P）
# ---------------------------------------------------------------------------


def perturb_drop_tool_result(run: Run, j: int) -> Run:
    """P4: 最後のステップの input_tokens から τ_j を引いた写しを返す（元の run は変えない）。

    「最後のリクエストから、それより前のツール結果 j を 1 つ除いた」履歴の切り詰めを模す。
    j は 0..n−3（最後のリクエストに載っているが、直前のステップのものではない結果）。
    τ_j は元の run から計算する。τ_j が 0 以下でも引く（適格性は plant_eligible で判定する）。
    """
    n = len(run.steps)
    if n < 3:
        raise ValueError(f"P4 は 3 ステップ以上の run にだけ適用できる（n_steps={n}）")
    if not 0 <= j <= n - 3:
        raise ValueError(f"j は 0..{n - 3} の範囲で指定する（j={j}）")
    tau_j = _tau(run, j)
    copy = run.model_copy(deep=True)
    copy.steps[-1].usage.input_tokens -= tau_j
    return copy


def _tau(run: Run, k: int) -> int:
    """τ_k = Δ_{k+1} − out_k。"""
    t_next = _total_input(run.steps[k + 1])
    t_this = _total_input(run.steps[k])
    return t_next - t_this - run.steps[k].usage.output_tokens


def plant_relative_effect(
    run: Run, prices: UnitPrices, plant: Literal["P2", "P3", "P4"], *, j: int | None = None
) -> float:
    """植込みが恒等式に与える相対的な影響を、植込み自体から計算する（EC-B2P の適格条件）。

    P2: 落とした再送分の課金 / C_r。
    P3: 書き込み区間を通常入力の単価にしたことによる課金の差 / C_r。
    P4: 台帳（植込み前の実費）と植込み後の usage の差 = τ_j × 通常入力の単価 / C_r。
    C_r は植込み前の run の直接計算の額。データの検出結果は使わない。
    """
    clean = allocate(run, prices)
    if clean.direct_usd == 0.0:
        return 0.0
    if plant == "P2":
        return abs(clean.assistant_resend_usd) / clean.direct_usd
    if plant == "P3":
        planted = allocate(run, prices, plant=AllocationPlant(cache_write_as_input=True))
        return abs(planted.allocated_usd - clean.allocated_usd) / clean.direct_usd
    if j is None:
        raise ValueError("P4 には j が要る")
    if len(run.steps) < 3 or not 0 <= j <= len(run.steps) - 3:
        raise ValueError(f"P4 の j が範囲外: j={j}, n_steps={len(run.steps)}")
    return abs(_tau(run, j)) * prices.input / _MTOK / clean.direct_usd


def plant_eligible(
    run: Run, prices: UnitPrices, plant: Literal["P2", "P3", "P4"], *, j: int | None = None
) -> bool:
    """EC-B2P の適格条件。

    P2・P3: 恒等式への相対的な影響が許容値の 2 倍（0.002）以上。
    P4: 3 ステップ以上で、τ_j > 0 かつ τ_j が最後の増分を上回り（植込みで負の増分が生じることが
    植込み自体から分かる）、元の run に負の増分が無い（元からの負の増分を植込みの検出と数えない）。
    """
    if plant in ("P2", "P3"):
        return plant_relative_effect(run, prices, plant) >= PLANT_ELIGIBLE_EFFECT
    n = len(run.steps)
    if n < 3 or j is None or not 0 <= j <= n - 3:
        return False
    totals = [_total_input(s) for s in run.steps]
    if any(totals[m] < totals[m - 1] for m in range(1, n)):
        return False
    tau = _tau(run, j)
    return tau > 0 and tau > totals[-1] - totals[-2]


# ---------------------------------------------------------------------------
# EC-B2: 帰属・反実仮想・履歴キャッシュ
# ---------------------------------------------------------------------------


def cca_of_tool(alloc: RunAllocation, tool: str) -> float:
    """ツール名に帰属する CCA の和（呼ばれていなければ 0.0）。"""
    return alloc.cca_by_tool.get(tool, 0.0)


def cca_of_step(alloc: RunAllocation, k: int) -> float:
    """ステップ k の CCA。最後のステップの出力は再送されないので 0.0。範囲外は IndexError。"""
    if not 0 <= k < alloc.n_steps:
        raise IndexError(f"ステップ {k} は範囲外（n_steps={alloc.n_steps}）")
    return alloc.cca_usd.get(k, 0.0)


class RatioEstimate(BaseModel):
    value: float
    ci_low: float
    ci_high: float
    n_clusters: int


def _mean(xs: Sequence[float]) -> float:
    return float(np.mean(xs))


def verbose_attribution(
    base_by_task: Mapping[str, Sequence[RunAllocation]],
    treated_by_task: Mapping[str, Sequence[RunAllocation]],
    *,
    tool: str = "run_tests",
    n_boot: int = 2000,
    seed: int,
) -> RatioEstimate:
    """Σ_t N_t / Σ_t D_t（EC-B2.md の定義）。区間はタスク単位のクラスタ・ブートストラップ。

    D_t = Cost(処置, t) − 平均 Cost(基準, t)、N_t = CCA_tool(処置, t) − 平均 CCA_tool(基準, t)。
    各タスクの値は反復の平均。両方に反復があるタスクだけを使う。Cost は直接計算の額。
    """
    tasks = sorted(t for t in base_by_task if t in treated_by_task)
    tasks = [t for t in tasks if len(base_by_task[t]) > 0 and len(treated_by_task[t]) > 0]
    if not tasks:
        raise ValueError("基準と処置の両方に反復があるタスクが無い")
    d = np.array(
        [
            _mean([a.direct_usd for a in treated_by_task[t]])
            - _mean([a.direct_usd for a in base_by_task[t]])
            for t in tasks
        ]
    )
    nn = np.array(
        [
            _mean([cca_of_tool(a, tool) for a in treated_by_task[t]])
            - _mean([cca_of_tool(a, tool) for a in base_by_task[t]])
            for t in tasks
        ]
    )
    if d.sum() == 0.0:
        raise ValueError("Σ D_t が 0 で比が定義できない")
    value = float(nn.sum() / d.sum())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(tasks), size=(n_boot, len(tasks)))
    d_sum = d[idx].sum(axis=1)
    n_sum = nn[idx].sum(axis=1)
    ok = d_sum != 0.0
    if not ok.any():
        raise ValueError("ブートストラップの全標本で Σ D_t が 0")
    ratios = n_sum[ok] / d_sum[ok]
    lo, hi = np.percentile(ratios, [2.5, 97.5])
    return RatioEstimate(value=value, ci_low=float(lo), ci_high=float(hi), n_clusters=len(tasks))


def _measured_prefix_usd(run: Run, prices: UnitPrices, prefix_tokens: int) -> list[float]:
    """リクエストごとの接頭辞（system＋ツール定義）の課金の実測値（usage の区間で計算）。"""
    out: list[float] = []
    for s in run.steps:
        f = _cum_price(s, prices)
        out.append(f(prefix_tokens) - f(0))
    return out


def counterfactual_cost(
    run: Run,
    prices: UnitPrices,
    *,
    history_cache: bool,
    prefix_tokens: int | None = None,
) -> float:
    """キャッシュ関係の usage を使わず、Δ と output_tokens だけから費用を計算する（EC-B2 H3）。

    接頭辞（prefix）の課金はステップごとの実測値をそのまま使う。
    history_cache=False: 接頭辞より後ろはすべて通常入力。
    True: 各ブロック（user0、k 番目の a<k>＋t<k>）は、最初に現れるリクエストでキャッシュ書き込み、
    以降は読み出し。出力は output_tokens × 出力単価。

    prefix_tokens: system＋ツール定義だけのトークン数。None のときは T0 − in0 を使う（off の run で
    だけ正しい。on の run は in0 がほぼ 0 で user0 が接頭辞に入り、Ĉoff で user0 が通常入力として
    数えられず節約が過小になる）。与えられたときは user0 = T0 − prefix_tokens として履歴ブロックに入れ、
    Ĉoff では毎ステップ通常入力、Ĉon では初回書き込み・以降読み出しで数える。
    """
    steps = run.steps
    n = len(steps)
    if n == 0:
        return 0.0
    totals = [_total_input(s) for s in steps]
    prefix_tokens, user0 = _prefix_and_user0(run, prefix_tokens)
    prefix_measured = _measured_prefix_usd(run, prices, prefix_tokens)
    # ブロック k = a<k> と t<k> を合わせた長さ = Δ_{k+1}
    deltas = [totals[k + 1] - totals[k] for k in range(n - 1)]
    total = 0.0
    for m in range(n):
        total += prefix_measured[m] + steps[m].usage.output_tokens * prices.output / _MTOK
        if not history_cache:
            beyond_prefix = user0 + sum(deltas[:m])
            total += beyond_prefix * prices.input / _MTOK
            continue
        total += user0 * (prices.cache_write if m == 0 else prices.cache_read) / _MTOK
        for k in range(m):
            p = prices.cache_write if k + 1 == m else prices.cache_read
            total += deltas[k] * p / _MTOK
    return total


class CachePredictionError(BaseModel):
    """EC-B2 H3（G0 項目 13 の後の定義）。"""

    e_on: float  # Σ_{on} (L_j − Ĉon_j) / Σ_{on} Ŝ_j
    e_off: float  # Σ_{off} (L_k − Ĉoff_k) / Σ_{off} Ŝ_k
    error: float  # |e_off − e_on| = history_cache_prediction_error
    n_on: int
    n_off: int
    prefix_tokens: int  # 予測に使った接頭辞（system＋ツール定義）のトークン数


def _residual_ratio(
    runs: Sequence[Run],
    ledger_usd: Mapping[str, float],
    prices: UnitPrices,
    *,
    on: bool,
    prefix_tokens: int,
) -> float:
    """Σ (L − Ĉ) / Σ Ŝ。Ĉ は on の run なら Ĉon、off の run なら Ĉoff。Ŝ = Ĉoff − Ĉon。"""
    residual = 0.0
    predicted = 0.0
    for r in runs:
        c_off = counterfactual_cost(r, prices, history_cache=False, prefix_tokens=prefix_tokens)
        c_on = counterfactual_cost(r, prices, history_cache=True, prefix_tokens=prefix_tokens)
        predicted += c_off - c_on
        residual += ledger_usd[r.run_id] - (c_on if on else c_off)
    if predicted <= 0.0:
        raise ValueError(f"予測節約の和が 0 以下で相対誤差が定義できない（{predicted}）")
    return residual / predicted


def history_cache_prediction_error(
    on_runs: Sequence[Run],
    off_runs: Sequence[Run],
    ledger_usd: Mapping[str, float],
    prices: UnitPrices,
    *,
    prefix_tokens: int | None = None,
) -> CachePredictionError:
    """履歴キャッシュの節約予測の誤差（EC-B2 H3）。

    Ŝ_j = Ĉoff_j − Ĉon_j は counterfactual_cost で計算する（キャッシュ関係の usage を使わない）。
    on の run は L − Ĉon、off の run は L − Ĉoff を残差とし、それぞれ Σ Ŝ で割って e_on・e_off を出す。
    error = |e_off − e_on|。改訂前の定義 |Σ S − Σ Ŝ| / Σ Ŝ（S_j = Ĉoff_j − L_j、on の run だけ）は |e_on|
    に等しい。ledger_usd は run_id -> 台帳の usd の和（無ければ KeyError）。
    どちらかの群が空、または予測節約の和が 0 以下なら ValueError。

    prefix_tokens: system＋ツール定義のトークン数。on の run は user0 にも区切りが付いて in0 が
    ほぼ 0 になるので、T0 − in0 を使わない。None のときは off_runs のステップ 0 の T0 − in0 から求め、
    off_runs の間で一致しなければ ValueError（版が違えば呼び出し側が明示的に渡す）。
    """
    if not on_runs or not off_runs:
        raise ValueError("on_runs と off_runs の両方が必要")
    if prefix_tokens is None:
        found = {_prefix_and_user0(r, None)[0] for r in off_runs}
        if len(found) != 1:
            raise ValueError(
                f"off_runs の接頭辞のトークン数が一致しない（{sorted(found)}）。prefix_tokens を渡す"
            )
        prefix_tokens = found.pop()
    e_on = _residual_ratio(on_runs, ledger_usd, prices, on=True, prefix_tokens=prefix_tokens)
    e_off = _residual_ratio(off_runs, ledger_usd, prices, on=False, prefix_tokens=prefix_tokens)
    return CachePredictionError(
        e_on=e_on,
        e_off=e_off,
        error=abs(e_off - e_on),
        n_on=len(on_runs),
        n_off=len(off_runs),
        prefix_tokens=prefix_tokens,
    )


def predicted_resend_ratio(n: int, prices: UnitPrices) -> float:
    """n 回再送される出力の費用比 on / off = (p_w + p_r (n−1)) / (p_in n)。n=10 で約 0.215。"""
    if n < 1:
        raise ValueError(f"n は 1 以上（n={n}）")
    return (prices.cache_write + prices.cache_read * (n - 1)) / (prices.input * n)


def cca_ratio(late: RunAllocation, late_step: int, early: RunAllocation, early_step: int) -> float:
    """control_attribution = CCA_L / CCA_E（EC-B2.md 対照 (a)）。"""
    denom = cca_of_step(early, early_step)
    if denom == 0.0:
        raise ValueError("早い側の CCA が 0 で比が定義できない")
    return cca_of_step(late, late_step) / denom


# ---------------------------------------------------------------------------
# 台帳と trace の対応づけ（SPEC.md 10 節、K3）
# ---------------------------------------------------------------------------


class RunLedgerMatch(BaseModel):
    run_id: str
    n_steps: int
    matched_steps: list[int]
    unmatched_steps: list[int]  # 対応する行が無かったステップ
    fully_matched: bool
    ledger_usd: float  # 対応した行の usd の和
    naive_usd: float  # run_id が同じ行の usd の総和
    unmatched_ledger_rows: int
    duplicate_identical_rows: int


class LedgerMatchReport(BaseModel):
    runs: dict[str, RunLedgerMatch]
    ledger_rows_considered: int  # 指定 mode かつ run_id がいずれかの run と同じ行の数
    ledger_match_rate: float  # fully_matched の run 数 / run 数（run 単位）
    step_match_rate: float  # 対応したステップ数 / 全ステップ数（記録のみ）


def _token_key(e: LedgerEntry) -> tuple[int, int, int, int, int]:
    return (
        e.step,
        e.input_tokens,
        e.output_tokens,
        e.cache_read_input_tokens,
        e.cache_creation_input_tokens,
    )


def match_ledger(
    runs: Sequence[Run],
    ledger: Sequence[LedgerEntry],
    *,
    modes: Collection[str] = ("live",),
) -> LedgerMatchReport:
    """台帳の行と trace のステップを (run_id, step, 4 つのトークン数) の完全一致で対応づける。

    1 ステップには 1 行だけを対応させる（候補が複数なら台帳のファイル順で最初の行）。残りの同一内容の
    行は duplicate_identical_rows、どのステップにも対応しなかった行は unmatched_ledger_rows に数える。
    """
    wanted = {r.run_id for r in runs}
    if len(wanted) != len(runs):
        raise ValueError("runs に同じ run_id が重複している")
    by_run: dict[str, list[LedgerEntry]] = {}
    for e in ledger:
        if e.mode in modes and e.run_id in wanted:
            by_run.setdefault(e.run_id, []).append(e)

    result: dict[str, RunLedgerMatch] = {}
    considered = 0
    for run in runs:
        rows = by_run.get(run.run_id, [])
        considered += len(rows)
        first_by_key: dict[tuple[int, int, int, int, int], LedgerEntry] = {}
        n_by_key: dict[tuple[int, int, int, int, int], int] = {}
        for e in rows:
            k = _token_key(e)
            first_by_key.setdefault(k, e)
            n_by_key[k] = n_by_key.get(k, 0) + 1
        matched: list[int] = []
        unmatched: list[int] = []
        step_keys: set[tuple[int, int, int, int, int]] = set()
        ledger_usd = 0.0
        duplicates = 0
        for s in run.steps:
            key = (
                s.i,
                s.usage.input_tokens,
                s.usage.output_tokens,
                s.usage.cache_read_input_tokens,
                s.usage.cache_creation_input_tokens,
            )
            step_keys.add(key)
            if key in first_by_key:
                matched.append(s.i)
                ledger_usd += first_by_key[key].usd
                duplicates += n_by_key[key] - 1
            else:
                unmatched.append(s.i)
        unmatched_rows = sum(1 for e in rows if _token_key(e) not in step_keys)
        result[run.run_id] = RunLedgerMatch(
            run_id=run.run_id,
            n_steps=len(run.steps),
            matched_steps=matched,
            unmatched_steps=unmatched,
            fully_matched=len(run.steps) > 0 and not unmatched,
            ledger_usd=ledger_usd,
            naive_usd=sum(e.usd for e in rows),
            unmatched_ledger_rows=unmatched_rows,
            duplicate_identical_rows=duplicates,
        )

    n_runs = len(runs)
    total_steps = sum(m.n_steps for m in result.values())
    matched_steps = sum(len(m.matched_steps) for m in result.values())
    return LedgerMatchReport(
        runs=result,
        ledger_rows_considered=considered,
        ledger_match_rate=(sum(m.fully_matched for m in result.values()) / n_runs)
        if n_runs
        else 0.0,
        step_match_rate=(matched_steps / total_steps) if total_steps else 0.0,
    )
