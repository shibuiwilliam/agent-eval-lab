"""正味価値 NV・ロバスト性 Rob・損益分岐・同時信頼帯（SPEC.md 9.3 節、EC-R1.md、THETA.md）。

式は NEW_IDEA 4.0 節:
  E[C_del] = C_a + π_S R(Δ_S) + π_F (R_F + H') + π_W (R(Δ_W) + r H' + (1 − r) D)
  R(Δ) = R_0 + ρ |Δ|、NV = H − E[C_del]

Θ（仮定パラメータの範囲）は ``coding/theta.yaml`` にだけ書かれ、ここでは引数として受け取る。
NV の差は 6 つの θ の特徴量の 1 次式になるので、Θ の 200,000 標本でも行列積だけで計算できる
（標本 × 複製の巨大な行列は作らず、θ の方向にチャンクで走査する）。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, get_args

import numpy as np
import yaml
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenteval.core.coding_types import THETA_PATH
from agenteval.core.schema import Run
from agenteval.econ.outcome import classify

ThetaName = Literal["H", "H_prime", "R0", "rho", "R_F", "D", "r"]
BaseParamName = Literal["H", "kappa", "R0", "rho", "R_F", "D", "miss"]  # 独立に引く 7 つ
_BASE_PARAM_ORDER: Final = get_args(BaseParamName)
_EXPECTED_DERIVED: Final = {"H_prime": "kappa * H", "r": "1 - miss"}

FloatArray = NDArray[np.float64]


class Theta(BaseModel):
    """4.0 節の式が使う仮定パラメータの 1 点（USD、r は 0〜1）。H_prime = kappa * H、r = 1 - miss から作る。"""

    H: float
    H_prime: float
    R0: float
    rho: float
    R_F: float
    D: float
    r: float


class ParamRange(BaseModel):
    """1 パラメータの範囲と分布。"""

    model_config = ConfigDict(extra="forbid")

    low: float = Field(gt=0)
    high: float = Field(gt=0)
    scale: Literal["log", "linear"]

    @model_validator(mode="after")
    def _ordered(self) -> ParamRange:
        if self.low > self.high:
            raise ValueError(f"low({self.low}) > high({self.high})")
        return self


class ThetaVariant(BaseModel):
    """感度の変種（記録のみ）。log_width_factor は対数尺度で中心（幾何平均）のまわりの幅の倍率。

    scale があれば全軸をその分布にする。clip は変種を当てた後の範囲を切り詰める。
    """

    model_config = ConfigDict(extra="forbid")

    log_width_factor: float = Field(default=1.0, gt=0)
    scale: Literal["log", "linear"] | None = None
    clip: dict[BaseParamName, tuple[float, float]] = Field(default_factory=dict)


class ThetaSpace(BaseModel):
    """coding/theta.yaml の内容。"""

    model_config = ConfigDict(extra="forbid")

    version: int
    status: Literal["draft", "approved"]
    seed: int
    n_samples: int = Field(ge=10_000)  # ADR-032: モンテカルロ誤差 <= 0.005
    params: dict[BaseParamName, ParamRange]
    derived: dict[str, str]
    variants: dict[str, ThetaVariant] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ThetaSpace:
        missing = [p for p in _BASE_PARAM_ORDER if p not in self.params]
        if missing:
            raise ValueError(f"params に {missing} が無い（7 つすべてが必須）")
        if self.derived != _EXPECTED_DERIVED:
            raise ValueError(
                f"derived が {_EXPECTED_DERIVED} と一致しない: {self.derived}（式は解釈しない）"
            )
        return self


def load_theta_space(path: Path = THETA_PATH) -> ThetaSpace:
    """coding/theta.yaml を ThetaSpace として読む。形が合わなければ ValueError（pydantic）。"""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ThetaSpace.model_validate(raw)


def apply_variant(space: ThetaSpace, name: str) -> ThetaSpace:
    """variants[name] を当てた空間（seed・n_samples は同じ。variants は空にする）。

    log_width_factor は、各パラメータの範囲を対数尺度で幾何平均のまわりに f 倍の幅にする。
    その後 clip で切り詰め、scale が指定されていれば全軸の分布を差し替える。
    """
    if name not in space.variants:
        raise ValueError(f"変種 {name} が無い（あるのは {sorted(space.variants)}）")
    v = space.variants[name]
    params: dict[BaseParamName, ParamRange] = {}
    for p in _BASE_PARAM_ORDER:
        rng_ = space.params[p]
        low, high = rng_.low, rng_.high
        if v.log_width_factor != 1.0:  # 1 倍なら元の値のまま（対数の往復で誤差を入れない）
            lo_log, hi_log = math.log(low), math.log(high)
            center, half = (lo_log + hi_log) / 2.0, (hi_log - lo_log) / 2.0 * v.log_width_factor
            low, high = math.exp(center - half), math.exp(center + half)
        if p in v.clip:
            c_lo, c_hi = v.clip[p]
            low, high = max(low, c_lo), min(high, c_hi)
        params[p] = ParamRange(low=low, high=high, scale=v.scale or rng_.scale)
    return space.model_copy(update={"params": params, "variants": {}})


@dataclass(frozen=True)
class _ThetaArrays:
    """Θ の標本を列ごとの配列で持つ（NV の計算用）。"""

    H: FloatArray
    H_prime: FloatArray
    R0: FloatArray
    rho: FloatArray
    R_F: FloatArray
    D: FloatArray
    r: FloatArray

    def features(self) -> FloatArray:
        """NV の差を 1 次式にする特徴量 φ̃ = [1, R0, ρ, R_F, H', r H', (1 − r) D]（標本 × 7）。"""
        return np.column_stack(
            [
                np.ones_like(self.R0),
                self.R0,
                self.rho,
                self.R_F,
                self.H_prime,
                self.r * self.H_prime,
                (1.0 - self.r) * self.D,
            ]
        )


class ThetaSample(list[Theta]):
    """sample_thetas の戻り値。list[Theta] として使え、配列表現を持ち回る（変換を 1 回で済ませる）。"""

    arrays: _ThetaArrays

    def __init__(self, arrays: _ThetaArrays) -> None:
        n = len(arrays.H)
        super().__init__(
            Theta.model_construct(
                H=float(arrays.H[i]),
                H_prime=float(arrays.H_prime[i]),
                R0=float(arrays.R0[i]),
                rho=float(arrays.rho[i]),
                R_F=float(arrays.R_F[i]),
                D=float(arrays.D[i]),
                r=float(arrays.r[i]),
            )
            for i in range(n)
        )
        self.arrays = arrays
        self._features: FloatArray | None = None

    def features(self) -> FloatArray:
        if self._features is None:
            self._features = self.arrays.features()
        return self._features


def _draw_axis(rng: np.random.Generator, spec: ParamRange, n: int) -> FloatArray:
    if spec.scale == "log":
        return np.exp(rng.uniform(math.log(spec.low), math.log(spec.high), n))
    return rng.uniform(spec.low, spec.high, n)


def sample_thetas(space: ThetaSpace) -> list[Theta]:
    """space.seed の default_rng で n_samples 点を引く。各軸独立。log は対数一様、linear は一様。

    軸を引く順は BaseParamName の定義順（H, kappa, R0, rho, R_F, D, miss）に固定する。
    同じ space なら同じ標本（共通乱数）。
    """
    rng = np.random.default_rng(space.seed)
    n = space.n_samples
    cols = {p: _draw_axis(rng, space.params[p], n) for p in _BASE_PARAM_ORDER}
    arrays = _ThetaArrays(
        H=cols["H"],
        H_prime=cols["kappa"] * cols["H"],
        R0=cols["R0"],
        rho=cols["rho"],
        R_F=cols["R_F"],
        D=cols["D"],
        r=1.0 - cols["miss"],
    )
    return ThetaSample(arrays)


def _theta_features(thetas: Sequence[Theta]) -> FloatArray:
    """θ の列から特徴量行列を作る（ThetaSample なら変換済みを使う）。"""
    if len(thetas) == 0:
        raise ValueError("thetas が空")
    if isinstance(thetas, ThetaSample):
        return thetas.features()
    arr = _ThetaArrays(
        H=np.array([t.H for t in thetas], dtype=float),
        H_prime=np.array([t.H_prime for t in thetas], dtype=float),
        R0=np.array([t.R0 for t in thetas], dtype=float),
        rho=np.array([t.rho for t in thetas], dtype=float),
        R_F=np.array([t.R_F for t in thetas], dtype=float),
        D=np.array([t.D for t in thetas], dtype=float),
        r=np.array([t.r for t in thetas], dtype=float),
    )
    return arr.features()


# ======================================================================================
# 版の経済量
# ======================================================================================


class VersionEconomics(BaseModel):
    """版 1 つの NV の入力（測る量）。"""

    version_id: str
    n_runs: int
    pi_S: float  # タスク間の等重み平均（EC-R1.md）
    pi_W: float
    pi_F: float
    cost_usd: float  # 1 試行の平均費用 C_a（live の Run.cost.usd。タスク等重み）
    diff_lines_S: float  # S の試行の平均 review_diff_lines。S が 0 件なら W の値、両方 0 件なら 0
    diff_lines_W: float  # W の試行の平均 review_diff_lines。W が 0 件なら S の値


@dataclass
class _TaskStats:
    """1 タスクぶんの集計（ブートストラップで使い回す）。"""

    n: int = 0
    n_S: int = 0
    n_W: int = 0
    n_F: int = 0
    cost_sum: float = 0.0
    lines_S_sum: float = 0.0
    lines_W_sum: float = 0.0


def _aggregate(runs: Sequence[Run], diff_lines: Mapping[str, float]) -> dict[str, _TaskStats]:
    stats: dict[str, _TaskStats] = {}
    for run in runs:
        st = stats.setdefault(run.task_id, _TaskStats())
        cls = classify(run)
        st.n += 1
        st.cost_sum += run.cost.usd
        if cls == "F":
            st.n_F += 1
            continue
        if run.run_id not in diff_lines:
            raise ValueError(f"{run.run_id}: diff_lines に差分行数が無い（S / W の run には必須）")
        if cls == "S":
            st.n_S += 1
            st.lines_S_sum += diff_lines[run.run_id]
        else:
            st.n_W += 1
            st.lines_W_sum += diff_lines[run.run_id]
    return stats


def _economics_from_stats(
    version_id: str, stats: Mapping[str, _TaskStats], tasks: Sequence[str]
) -> VersionEconomics:
    if not tasks:
        raise ValueError("tasks が空")
    mult = Counter(tasks)
    unknown = [t for t in mult if t not in stats]
    if unknown:
        raise ValueError(f"{version_id}: runs に無いタスクが指定された: {unknown}")
    m = len(tasks)
    pi_s = sum(c * stats[t].n_S / stats[t].n for t, c in mult.items()) / m
    pi_w = sum(c * stats[t].n_W / stats[t].n for t, c in mult.items()) / m
    pi_f = sum(c * stats[t].n_F / stats[t].n for t, c in mult.items()) / m
    cost = sum(c * stats[t].cost_sum / stats[t].n for t, c in mult.items()) / m
    n_runs = sum(c * stats[t].n for t, c in mult.items())
    cnt_s = sum(c * stats[t].n_S for t, c in mult.items())
    cnt_w = sum(c * stats[t].n_W for t, c in mult.items())
    mean_s = sum(c * stats[t].lines_S_sum for t, c in mult.items()) / cnt_s if cnt_s else None
    mean_w = sum(c * stats[t].lines_W_sum for t, c in mult.items()) / cnt_w if cnt_w else None
    if mean_s is None and mean_w is None:
        mean_s = mean_w = 0.0
    elif mean_s is None:
        mean_s = mean_w
    elif mean_w is None:
        mean_w = mean_s
    assert mean_s is not None and mean_w is not None
    return VersionEconomics(
        version_id=version_id,
        n_runs=n_runs,
        pi_S=pi_s,
        pi_W=pi_w,
        pi_F=pi_f,
        cost_usd=cost,
        diff_lines_S=mean_s,
        diff_lines_W=mean_w,
    )


def economics_of(
    version_id: str,
    runs: Sequence[Run],
    diff_lines: Mapping[str, float],
    *,
    tasks: Sequence[str] | None = None,
) -> VersionEconomics:
    """runs から版の経済量を作る。

    π はタスクごとの頻度のタスク間等重み平均、C_a もタスクごとの平均のタスク等重み平均。
    差分行数は S（W）の run 全体の平均。tasks に重複を含むタスク列を渡すと、その多重度で重みづける
    （クラスタ・ブートストラップ用）。tasks が None なら runs のタスクを 1 回ずつ。
    """
    if not runs:
        raise ValueError(f"{version_id}: runs が空")
    stats = _aggregate(runs, diff_lines)
    return _economics_from_stats(version_id, stats, sorted(stats) if tasks is None else tasks)


def expected_delegation_cost(e: VersionEconomics, theta: Theta) -> float:
    """E[C_del] = C_a + π_S R(Δ_S) + π_F (R_F + H') + π_W (R(Δ_W) + r H' + (1 − r) D)。"""
    r_s = theta.R0 + theta.rho * e.diff_lines_S
    r_w = theta.R0 + theta.rho * e.diff_lines_W
    return (
        e.cost_usd
        + e.pi_S * r_s
        + e.pi_F * (theta.R_F + theta.H_prime)
        + e.pi_W * (r_w + theta.r * theta.H_prime + (1.0 - theta.r) * theta.D)
    )


def net_value(e: VersionEconomics, theta: Theta) -> float:
    """NV = H − E[C_del]。"""
    return theta.H - expected_delegation_cost(e, theta)


def leak_rate(e: VersionEconomics, theta: Theta) -> float:
    """漏れ率 π_W (1 − r)。"""
    return e.pi_W * (1.0 - theta.r)


def breakeven_success_rate(e: VersionEconomics, theta: Theta) -> float:
    """π_W : π_F の比 q = π_W / (π_W + π_F) を固定して NV = 0 となる π_S。解が [0, 1] に無ければ nan。

    c_S = R(Δ_S)、c_W = R(Δ_W) + r H' + (1 − r) D、c_F = R_F + H' として
    π_S* = (q c_W + (1 − q) c_F + C_a − H) / (q c_W + (1 − q) c_F − c_S)。
    π_W + π_F = 0（q が決まらない）か分母が 0 以下なら nan。
    """
    tail = e.pi_W + e.pi_F
    if tail <= 0.0:
        return math.nan
    q = e.pi_W / tail
    c_s = theta.R0 + theta.rho * e.diff_lines_S
    c_w = (
        theta.R0 + theta.rho * e.diff_lines_W + theta.r * theta.H_prime + (1.0 - theta.r) * theta.D
    )
    c_f = theta.R_F + theta.H_prime
    mix = q * c_w + (1.0 - q) * c_f
    denom = mix - c_s
    if denom <= 0.0:
        return math.nan
    pi_s = (mix + e.cost_usd - theta.H) / denom
    return pi_s if 0.0 <= pi_s <= 1.0 else math.nan


def _cost_coefficients(e: VersionEconomics) -> FloatArray:
    """E[C_del] = x · φ̃（φ̃ は _ThetaArrays.features）となる係数ベクトル x（長さ 7）。"""
    return np.array(
        [
            e.cost_usd,
            e.pi_S + e.pi_W,
            e.pi_S * e.diff_lines_S + e.pi_W * e.diff_lines_W,
            e.pi_F,
            e.pi_F,
            e.pi_W,
            e.pi_W,
        ],
        dtype=float,
    )


def _nv_diff(a: VersionEconomics, b: VersionEconomics, features: FloatArray) -> FloatArray:
    """d(θ) = NV_a(θ) − NV_b(θ)（標本ごと）。H は打ち消す。"""
    return -(features @ (_cost_coefficients(a) - _cost_coefficients(b)))


def robustness(a: VersionEconomics, b: VersionEconomics, thetas: Sequence[Theta]) -> float:
    """Rob(a ≻ b) = NV_a(θ) > NV_b(θ) となる θ の割合（点推定。不確かさを持たない）。"""
    return float(np.mean(_nv_diff(a, b, _theta_features(thetas)) > 0.0))


def paired_economics_bootstrap(
    runs_a: Sequence[Run],
    runs_b: Sequence[Run],
    diff_lines: Mapping[str, float],
    *,
    n_boot: int = 2000,
    seed: int,
) -> list[tuple[VersionEconomics, VersionEconomics]]:
    """共通のタスクを復元抽出し（a と b で同じタスク列＝対にする）、各複製で economics_of を計算する。

    抽出の母集団は両方の版に runs があるタスクの積集合（ソート済み）。同じ seed なら同じ複製。
    """
    if not runs_a or not runs_b:
        raise ValueError("runs_a / runs_b が空")
    if n_boot < 1:
        raise ValueError("n_boot >= 1 が必要")
    stats_a, stats_b = _aggregate(runs_a, diff_lines), _aggregate(runs_b, diff_lines)
    common = sorted(set(stats_a) & set(stats_b))
    if not common:
        raise ValueError("a と b に共通のタスクが無い")
    va, vb = runs_a[0].version_id, runs_b[0].version_id
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(common), size=(n_boot, len(common)))
    out: list[tuple[VersionEconomics, VersionEconomics]] = []
    for row in idx:
        sample = [common[i] for i in row]
        out.append(
            (
                _economics_from_stats(va, stats_a, sample),
                _economics_from_stats(vb, stats_b, sample),
            )
        )
    return out


def confident_fraction(
    replicates: Sequence[tuple[VersionEconomics, VersionEconomics]],
    point: tuple[VersionEconomics, VersionEconomics],
    thetas: Sequence[Theta],
    *,
    direction: Literal["a_over_b", "either"],
    alpha: float = 0.05,
) -> float:
    """d(θ) = NV_a(θ) − NV_b(θ) の同時信頼帯（sup-t）が 0 を含まない θ の割合。

    帯: d̂(θ) ± c* · sd(θ)。sd(θ) は複製の d*(θ) の標本標準偏差（ddof = 1）、
    c* は複製ごとの max_θ |d*(θ) − d̂(θ)| / sd(θ) の 1 − α 分位点。
    a_over_b は帯の下限 > 0 の θ だけ、either は |d̂| > c* sd の θ を数える。
    sd(θ) = 0 の θ は c* の計算から除く（その θ では帯の幅が 0）。

    d は特徴量の 1 次式なので、sd(θ) は係数ベクトルの共分散（7 × 7）から、max は θ 方向のチャンク走査で
    求める（複製 × θ の行列を一度に作らない）。EC-R1 の null_confident_fraction_c12（either）と
    confident_fraction_c01_over_c13（a_over_b）。
    """
    if len(replicates) < 2:
        raise ValueError("複製が 2 つ以上要る")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha は (0, 1)")
    feats = _theta_features(thetas)  # T × 7
    x_hat = _cost_coefficients(point[0]) - _cost_coefficients(point[1])
    x_rep = np.array(
        [_cost_coefficients(a) - _cost_coefficients(b) for a, b in replicates], dtype=float
    )  # B × 7
    cov = np.cov(x_rep, rowvar=False, ddof=1)  # 7 × 7
    var = np.einsum("ti,ij,tj->t", feats, cov, feats)
    sd = np.sqrt(np.clip(var, 0.0, None))
    positive = sd > 0.0
    u = x_hat - x_rep  # d*(θ) − d̂(θ) = −(x* − x̂)·φ̃ = (x̂ − x*)·φ̃ の係数
    psi = np.where(positive[:, None], feats / np.where(positive, sd, 1.0)[:, None], 0.0)  # T × 7
    sup = np.zeros(len(replicates))
    chunk = max(1, 4_000_000 // len(replicates))
    for start in range(0, psi.shape[0], chunk):
        block = np.abs(u @ psi[start : start + chunk].T)  # B × chunk
        sup = np.maximum(sup, block.max(axis=1))
    c_star = float(np.quantile(sup, 1.0 - alpha))
    d_hat = -(feats @ x_hat)
    if direction == "a_over_b":
        return float(np.mean(d_hat - c_star * sd > 0.0))
    return float(np.mean(np.abs(d_hat) > c_star * sd))


def rank_reversal(visible_diff_ci_high: float, rob_a_over_b: float) -> int:
    """EC-R1 の visible_vs_nv_rank_reversal。

    可視合格率の差（b − a）の 95% 区間の上限が 0 以上で、かつ rob_a_over_b > 0.5 なら 1、それ以外 0
    （a = c01、b = c13）。nan は 0。
    """
    return int(visible_diff_ci_high >= 0.0 and rob_a_over_b > 0.5)
