"""逐次確率比検定（原典 3.7）と Monte Carlo 検証（API 不要）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Decision = Literal["continue", "accept", "reject"]


@dataclass
class SPRT:
    """`H0: p = p0`（悪い）対 `H1: p = p1`（良い）の逐次検定。

    `accept` = H1 採択（合格率が p1 側）、`reject` = H0 採択（p0 側）。
    """

    p0: float = 0.5
    p1: float = 0.9
    alpha: float = 0.05
    beta: float = 0.10
    llr: float = 0.0
    n: int = 0
    successes: int = 0
    history: list[float] = field(default_factory=list)

    @property
    def upper(self) -> float:
        return math.log((1 - self.beta) / self.alpha)

    @property
    def lower(self) -> float:
        return math.log(self.beta / (1 - self.alpha))

    def update(self, success: bool) -> Decision:
        """1 試行を反映して判定を返す。"""
        self.n += 1
        self.successes += int(success)
        if success:
            self.llr += math.log(self.p1 / self.p0)
        else:
            self.llr += math.log((1 - self.p1) / (1 - self.p0))
        self.history.append(self.llr)
        if self.llr >= self.upper:
            return "accept"
        if self.llr <= self.lower:
            return "reject"
        return "continue"

    def reset(self) -> None:
        self.llr = 0.0
        self.n = 0
        self.successes = 0
        self.history.clear()


def fixed_n_for_power(p0: float, p1: float, alpha: float, beta: float) -> int:
    """同じ検出力を持つ固定 n 法の試行数（正規近似）。"""
    from scipy.stats import norm

    z_a = float(norm.ppf(1 - alpha))
    z_b = float(norm.ppf(1 - beta))
    pooled = (p0 + p1) / 2
    numerator = z_a * math.sqrt(pooled * (1 - pooled)) + z_b * math.sqrt(p1 * (1 - p1))
    return max(1, math.ceil((numerator / (p1 - p0)) ** 2))


def monte_carlo(
    p_true: float,
    p0: float = 0.5,
    p1: float = 0.9,
    alpha: float = 0.05,
    beta: float = 0.10,
    trials: int = 10_000,
    max_n: int = 200,
    seed: int = 20260913,
) -> dict[str, float]:
    """経験的な誤り率と平均試行数（来歴ラベル: simulated）。"""
    rng = np.random.default_rng(seed)
    decisions: list[str] = []
    lengths: list[int] = []
    for _ in range(trials):
        test = SPRT(p0=p0, p1=p1, alpha=alpha, beta=beta)
        decision: Decision = "continue"
        for _ in range(max_n):
            decision = test.update(bool(rng.random() < p_true))
            if decision != "continue":
                break
        decisions.append(decision)
        lengths.append(test.n)
    accept = decisions.count("accept") / trials
    reject = decisions.count("reject") / trials
    undecided = decisions.count("continue") / trials
    return {
        "p_true": p_true,
        "accept_rate": accept,
        "reject_rate": reject,
        "undecided_rate": undecided,
        "mean_n": float(np.mean(lengths)),
        "fixed_n": float(fixed_n_for_power(p0, p1, alpha, beta)),
    }


def error_rates(
    p0: float = 0.5, p1: float = 0.9, alpha: float = 0.05, beta: float = 0.10, **kwargs: object
) -> dict[str, float]:
    """名目 α / β に対する経験的な誤り率。

    α = H0 が真（p=p0）なのに accept した割合、β = H1 が真（p=p1）なのに reject した割合。
    """
    under_h0 = monte_carlo(p0, p0=p0, p1=p1, alpha=alpha, beta=beta, **kwargs)  # type: ignore[arg-type]
    under_h1 = monte_carlo(p1, p0=p0, p1=p1, alpha=alpha, beta=beta, **kwargs)  # type: ignore[arg-type]
    return {
        "empirical_alpha": under_h0["accept_rate"],
        "empirical_beta": under_h1["reject_rate"],
        "mean_n_h0": under_h0["mean_n"],
        "mean_n_h1": under_h1["mean_n"],
        "fixed_n": under_h0["fixed_n"],
    }
