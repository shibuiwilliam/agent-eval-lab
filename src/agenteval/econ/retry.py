"""実現可能な pass@k・試行プールの再標本化・反復の独立性の診断（SPEC.md 9.4 節、EC-R2.md）。

方策: 「可視テストが緑（S か W）になった最初の試行を採用し、最大 k 回」。
タスクの 1 試行の類確率を (s, w, f) とすると、閉形式は
  P_k(成功) = s (1 − f^k) / (1 − f)、P_k(静かな誤り) = w (1 − f^k) / (1 − f)、pass@k = 1 − (1 − s)^k、
  G_k = pass@k − P_k(成功)。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel

from agenteval.core.schema import Run
from agenteval.econ.outcome import OutcomeClass
from agenteval.econ.roi import Theta

_SUM_TOL = 1e-9


class RealizedK(BaseModel):
    """k 回まで再試行する方策の値。"""

    k: int
    p_success: float  # P_k(成功)
    p_silent_wrong: float  # P_k(静かな誤り)
    expected_trials: float
    pass_at_k: float  # 1 − (1 − s)^k
    gap: float  # G_k = pass@k − P_k(成功)


def _geometric_sum(f: float, k: int) -> float:
    """Σ_{j=0}^{k-1} f^j = (1 − f^k) / (1 − f)。f = 1 のときは k。"""
    if 1.0 - f < 1e-12:
        return float(k)
    return (1.0 - f**k) / (1.0 - f)


def realized_closed_form(s: float, w: float, f: float, k: int) -> RealizedK:
    """NEW_IDEA EC-R2 の閉形式。f = 1 のとき (1 − f^k) / (1 − f) は k とする。

    s + w + f が 1 から 1e-9 以上ずれる、または確率が負、k < 1 のとき ValueError。
    """
    if k < 1:
        raise ValueError("k >= 1 が必要")
    if min(s, w, f) < -_SUM_TOL:
        raise ValueError(f"確率が負: s={s}, w={w}, f={f}")
    if abs(s + w + f - 1.0) >= _SUM_TOL:
        raise ValueError(f"s + w + f = {s + w + f} が 1 でない")
    m = _geometric_sum(f, k)
    p_success = s * m
    pass_k = 1.0 - (1.0 - s) ** k
    return RealizedK(
        k=k,
        p_success=p_success,
        p_silent_wrong=w * m,
        expected_trials=m,
        pass_at_k=pass_k,
        gap=pass_k - p_success,
    )


def _enumerate_policy(n_s: int, n_w: int, n_f: int, k: int) -> tuple[float, float, float]:
    """試行 n 個から k 個を順序付きで非復元に選ぶすべての選び方の平均（U 統計量）。

    (P(S を採用), P(W を採用), 期待試行数)。ランダムな順序の列と同じ分布なので、逐次の確率で閉じた形に書ける:
    j 回目で初めて緑になる確率 = (先頭 j − 1 個が F) × (j 回目が S または W)。
    """
    n = n_s + n_w + n_f
    p_s = p_w = trials = 0.0
    prefix_f = 1.0  # 先頭 j − 1 個がすべて F の確率
    for j in range(1, k + 1):
        remaining = n - (j - 1)
        trials += prefix_f  # j 回目を実行する確率
        p_s += prefix_f * n_s / remaining
        p_w += prefix_f * n_w / remaining
        prefix_f *= max(n_f - (j - 1), 0) / remaining
    return p_s, p_w, trials


def realized_empirical(
    trials: Sequence[OutcomeClass],
    k: int,
    *,
    sampling: Literal["enumerate", "bootstrap"],
    n_resamples: int = 1000,
    rng: np.random.Generator | None = None,
) -> RealizedK:
    """「可視テストが緑（S か W）になった最初の試行を採用、最大 k 回」の方策の経験値（EC-R2.md）。

    enumerate: 試行列から k 個を順序付きで非復元に選ぶすべての選び方の平均（U 統計量。k <= len(trials)、
    超えたら ValueError）。pass_at_k は Chen らの不偏推定量 1 − C(n − c, k) / C(n, k)（c = S の数）。
    bootstrap: 復元抽出 n_resamples 回（rng 必須）。pass_at_k は抽出列での経験値（S が 1 つでもあれば 1）。
    """
    n = len(trials)
    if n == 0:
        raise ValueError("trials が空")
    if k < 1:
        raise ValueError("k >= 1 が必要")
    if sampling == "enumerate":
        if k > n:
            raise ValueError(f"enumerate は k <= 試行数 が必要（k={k}, n={n}）")
        n_s, n_w = trials.count("S"), trials.count("W")
        p_s, p_w, expected = _enumerate_policy(n_s, n_w, n - n_s - n_w, k)
        pass_k = 1.0 - math.comb(n - n_s, k) / math.comb(n, k)
        return RealizedK(
            k=k,
            p_success=p_s,
            p_silent_wrong=p_w,
            expected_trials=expected,
            pass_at_k=pass_k,
            gap=pass_k - p_s,
        )
    if rng is None:
        raise ValueError("bootstrap には rng（numpy.random.default_rng(seed)）が必要")
    if n_resamples < 1:
        raise ValueError("n_resamples >= 1 が必要")
    code = np.array([{"S": 0, "W": 1, "F": 2}[t] for t in trials])
    drawn = code[rng.integers(0, n, size=(n_resamples, k))]  # n_resamples × k
    green = drawn < 2
    any_green = green.any(axis=1)
    first = green.argmax(axis=1)
    adopted = drawn[np.arange(n_resamples), first]
    p_s = float(np.mean(any_green & (adopted == 0)))
    p_w = float(np.mean(any_green & (adopted == 1)))
    expected = float(np.mean(np.where(any_green, first + 1, k)))
    pass_k = float(np.mean((drawn == 0).any(axis=1)))
    return RealizedK(
        k=k,
        p_success=p_s,
        p_silent_wrong=p_w,
        expected_trials=expected,
        pass_at_k=pass_k,
        gap=pass_k - p_s,
    )


class KComparison(BaseModel):
    """閉形式の予測と経験値の比較 1 行（版 × k × 量）。"""

    version_id: str
    k: int
    quantity: Literal["success", "silent_wrong"]
    predicted: float  # タスク平均
    empirical: float  # タスク平均
    abs_error: float


def closed_form_vs_empirical(
    version_id: str,
    trials_by_task: Mapping[str, Sequence[OutcomeClass]],
    ks: Sequence[int],
    *,
    n_estimate: int,
    n_resamples: int = 1000,
    seed: int,
) -> list[KComparison]:
    """各タスクの先頭 n_estimate 試行（反復番号の順）で (s, w, f) を推定し、残りで経験値を出す。

    経験値は k <= 残りの試行数なら enumerate、超えたら bootstrap（EC-R2.md: 前半 3・後半 3、k = 4 は復元抽出）。
    bootstrap の乱数は seed の default_rng 1 本を、タスク名の昇順 × ks の順に消費する（再現可能）。
    戻り値は ks の順に、各 k で success、silent_wrong の 2 行。
    closed_form_mae_k1to4 は quantity="success" の abs_error を k と版で平均したもの（I8 が集計）。
    """
    if not trials_by_task:
        raise ValueError("trials_by_task が空")
    if n_estimate < 1:
        raise ValueError("n_estimate >= 1 が必要")
    rng = np.random.default_rng(seed)
    tasks = sorted(trials_by_task)
    pred: dict[int, list[tuple[float, float]]] = {k: [] for k in ks}
    emp: dict[int, list[tuple[float, float]]] = {k: [] for k in ks}
    for task in tasks:
        seq = list(trials_by_task[task])
        if len(seq) <= n_estimate:
            raise ValueError(f"{task}: 試行数 {len(seq)} が n_estimate={n_estimate} 以下")
        head, rest = seq[:n_estimate], seq[n_estimate:]
        s_hat, w_hat = head.count("S") / n_estimate, head.count("W") / n_estimate
        f_hat = 1.0 - s_hat - w_hat
        for k in ks:
            p = realized_closed_form(s_hat, w_hat, f_hat, k)
            pred[k].append((p.p_success, p.p_silent_wrong))
            e = realized_empirical(
                rest,
                k,
                sampling="enumerate" if k <= len(rest) else "bootstrap",
                n_resamples=n_resamples,
                rng=rng,
            )
            emp[k].append((e.p_success, e.p_silent_wrong))
    rows: list[KComparison] = []
    for k in ks:
        for qi, quantity in enumerate(("success", "silent_wrong")):
            predicted = float(np.mean([v[qi] for v in pred[k]]))
            empirical = float(np.mean([v[qi] for v in emp[k]]))
            rows.append(
                KComparison(
                    version_id=version_id,
                    k=k,
                    quantity="success" if qi == 0 else "silent_wrong",
                    predicted=predicted,
                    empirical=empirical,
                    abs_error=abs(predicted - empirical),
                )
            )
    return rows


def empirical_by_task(
    trials_by_task: Mapping[str, Sequence[OutcomeClass]],
    k: int,
    *,
    tasks: Sequence[str] | None = None,
) -> RealizedK:
    """全試行を使う enumerate の経験値のタスク等重み平均（g3_diff、silent_wrong_gain 用）。

    tasks に重複を含むタスク列を渡すと、その多重度で重みづける（クラスタ・ブートストラップ用）。
    k より試行数の少ないタスクがあれば ValueError。
    """
    selected = sorted(trials_by_task) if tasks is None else list(tasks)
    if not selected:
        raise ValueError("タスクが空")
    per_task: dict[str, RealizedK] = {}
    for t in set(selected):
        if t not in trials_by_task:
            raise ValueError(f"trials_by_task に無いタスク: {t}")
        per_task[t] = realized_empirical(trials_by_task[t], k, sampling="enumerate")

    def mean(field: Callable[[RealizedK], float]) -> float:
        return float(np.mean([field(per_task[t]) for t in selected]))

    return RealizedK(
        k=k,
        p_success=mean(lambda r: r.p_success),
        p_silent_wrong=mean(lambda r: r.p_silent_wrong),
        expected_trials=mean(lambda r: r.expected_trials),
        pass_at_k=mean(lambda r: r.pass_at_k),
        gap=mean(lambda r: r.gap),
    )


def identity_error_w0(
    trials_by_task: Mapping[str, Sequence[OutcomeClass]],
    ks: Sequence[int],
    *,
    grid_step: float = 0.05,
) -> tuple[float, int]:
    """(最大誤差, 観測上 w = 0 のタスク数)。w = 0 のとき P_k(成功) = pass@k という恒等式の自己検査。

    観測上 W の無いタスクについて、閉形式どうし（頻度から (s, 0, f)）と経験値どうし（enumerate、
    k <= 試行数のときだけ）、および合成の格子 s ∈ {0, grid_step, …, 1}・w = 0 の閉形式で
    max |P_k(成功) − pass@k| を取る。格子を常に含むので観測上のタスクが無くても空虚にならない。
    """
    n_grid = round(1.0 / grid_step)
    if n_grid < 1 or abs(n_grid * grid_step - 1.0) > 1e-9:
        raise ValueError(f"grid_step={grid_step} は 1 を割り切る必要がある")
    worst = 0.0
    for k in ks:
        for i in range(n_grid + 1):
            s = i / n_grid
            cf = realized_closed_form(s, 0.0, 1.0 - s, k)
            worst = max(worst, abs(cf.p_success - cf.pass_at_k))
    n_w0 = 0
    for seq in trials_by_task.values():
        if "W" in seq or not seq:
            continue
        n_w0 += 1
        n = len(seq)
        s_hat = seq.count("S") / n
        for k in ks:
            cf = realized_closed_form(s_hat, 0.0, 1.0 - s_hat, k)
            worst = max(worst, abs(cf.p_success - cf.pass_at_k))
            if k <= n:
                em = realized_empirical(seq, k, sampling="enumerate")
                worst = max(worst, abs(em.p_success - em.pass_at_k))
    return worst, n_w0


def identical_trial_rate(runs: Sequence[Run], final_diff_hash: Mapping[str, str]) -> float:
    """同じ (タスク, 版) の反復の対のうち、ツール呼び出し列と最終差分のハッシュが完全に一致する対の割合。

    ツール呼び出し列は名前と正規化した引数（ToolCall.args_norm）の列。final_diff_hash は run_id -> sha256。
    EC-R2.md の反復の独立性の診断（塩が効いていれば 0 に近い。記録のみ）。対が 0 なら nan。
    """
    groups: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    for run in runs:
        if run.run_id not in final_diff_hash:
            raise ValueError(f"{run.run_id}: final_diff_hash に無い")
        calls = json.dumps(
            [[c.name, c.args_norm] for s in run.steps for c in s.tool_calls],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        sig = (calls, final_diff_hash[run.run_id])
        counts = groups.setdefault((run.task_id, run.version_id), {})
        counts[sig] = counts.get(sig, 0) + 1
    pairs = same = 0
    for counts in groups.values():
        total = sum(counts.values())
        pairs += total * (total - 1) // 2
        same += sum(c * (c - 1) // 2 for c in counts.values())
    return same / pairs if pairs else math.nan


def retry_threshold(
    s: float,
    theta: Theta,
    *,
    u: Callable[[Theta], tuple[float, float, float]],
    cost_usd: float,
) -> float:
    """w / s の閾値 (u_S − u_F − C_a / s) / (u_F − u_W)。

    u は θ から (u_S, u_W, u_F) を作る関数。W が F より悪い（u_W < u_F）とき、w / s がこれを超える
    エージェントでは再試行は 1 回目から ROI を下げる。s <= 0 か u_F = u_W なら nan。
    """
    if s <= 0.0:
        return math.nan
    u_s, u_w, u_f = u(theta)
    denom = u_f - u_w
    if denom == 0.0:
        return math.nan
    return (u_s - u_f - cost_usd / s) / denom
