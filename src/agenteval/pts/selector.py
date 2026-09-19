"""不確実性駆動のテスト選択（原典 3.4）。

履歴が乏しい前提で設計する。既定は Beta 事後、変更イベントが 3 件以上たまったら
ロジスティック回帰を学習して平均する。学習・評価は変更イベント単位の leave-one-out。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from agenteval.core.registry import get_task
from agenteval.core.schema import Change, Run


@dataclass
class Selection:
    """選択結果。"""

    selected: list[str]
    skipped: list[str]
    budget: float
    expected_escape: float
    priorities: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)


def beta_posterior(passes: int, fails: int) -> float:
    """Beta 事後平均 `(passes + 1) / (passes + fails + 2)`。"""
    return (passes + 1) / (passes + fails + 2)


def entropy(p: float) -> float:
    """2 値エントロピー（bit）。"""
    p = min(max(p, 1e-9), 1 - 1e-9)
    return float(-(p * math.log2(p) + (1 - p) * math.log2(1 - p)))


def history_stats(runs: list[Run]) -> dict[str, tuple[int, int]]:
    """タスク ID → (合格数, 不合格数)。同一版内の反復は反復数ぶん数える。"""
    stats: dict[str, list[int]] = {}
    for run in runs:
        entry = stats.setdefault(run.task_id, [0, 0])
        entry[0 if run.passed() else 1] += 1
    return {k: (v[0], v[1]) for k, v in stats.items()}


def cost_of(runs: list[Run]) -> dict[str, float]:
    """タスク ID → 平均トークン（コスト c(t)）。"""
    buckets: dict[str, list[int]] = {}
    for run in runs:
        buckets.setdefault(run.task_id, []).append(run.cost.total_tokens)
    return {k: float(np.mean(v)) if v else 1.0 for k, v in buckets.items()}


def tool_sequence(runs: list[Run], task_id: str) -> list[str]:
    """タスクの代表的なツール名列（最初の run）。"""
    for run in runs:
        if run.task_id == task_id:
            return run.tool_names()
    return []


def similarity(a: list[str], b: list[str]) -> float:
    """ツール名列の正規化編集距離の補数。"""
    if not a and not b:
        return 1.0
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return 1.0 - dp[n][m] / max(n, m, 1)


def features(
    task_id: str, runs: list[Run], change: Change, coverage: dict[str, set[str]]
) -> list[float]:
    """ロジスティック回帰の特徴量（直近合格率・反転回数・カバレッジ重なり・フレーク率）。"""
    from agenteval.pts.coverage import change_units

    subset = [r for r in runs if r.task_id == task_id]
    recent = [r.passed() for r in subset[-5:]]
    passes = sum(recent)
    flips = sum(1 for a, b in pairwise(recent) if a != b)
    overlap = len(coverage.get(task_id, set()) & change_units(change))
    flake = flips / max(1, len(recent) - 1)
    return [
        passes / max(1, len(recent)),
        float(flips),
        float(overlap),
        float(flake),
        float(len(subset)),
    ]


def estimate_pass_probability(
    task_id: str,
    runs: list[Run],
    change: Change,
    coverage: dict[str, set[str]],
    model: Any | None = None,
) -> float:
    """`p̂_t`。モデルがあれば Beta 事後と平均する。"""
    stats = history_stats(runs)
    passes, fails = stats.get(task_id, (0, 0))
    prior = beta_posterior(passes, fails)
    if model is None:
        return prior
    x = np.array([features(task_id, runs, change, coverage)])
    predicted = float(model.predict_proba(x)[0][1])
    return (prior + predicted) / 2


def fit_model(
    events: list[tuple[Change, list[Run]]],
    coverage: dict[str, set[str]],
    holdout: int | None = None,
) -> Any | None:
    """変更イベント単位の学習。`holdout` のイベントは訓練から外す（leave-one-out）。"""
    from sklearn.linear_model import LogisticRegression

    rows: list[list[float]] = []
    labels: list[int] = []
    for index, (change, runs) in enumerate(events):
        if holdout is not None and index == holdout:
            continue
        for task_id in {r.task_id for r in runs}:
            rows.append(features(task_id, runs, change, coverage))
            subset = [r for r in runs if r.task_id == task_id]
            labels.append(int(all(r.passed() for r in subset)))
    if len(set(labels)) < 2 or len(rows) < 3:
        return None
    model = LogisticRegression(max_iter=500)
    model.fit(np.array(rows), np.array(labels))
    return model


def select(
    task_ids: list[str],
    runs: list[Run],
    change: Change,
    coverage: dict[str, set[str]],
    budget_ratio: float = 0.3,
    model: Any | None = None,
    redundancy_threshold: float = 0.8,
    epsilon: float = 0.0,
    seed: int = 20260913,
) -> Selection:
    """`H(p̂)/c` 降順の貪欲選択。

    原典のパイプラインは 3.2（カバレッジで候補を絞る）→ 3.4（不確実性で優先度を付ける）なので、
    候補集合に入るテストを先に並べ、予算が余ったら候補外を同じ基準で埋める。
    不可侵集合は選択ロジックの外で必ず含める（3.8）。
    """
    costs = cost_of(runs)
    mean_cost = float(np.mean(list(costs.values()))) if costs else 1.0
    budget_tokens = budget_ratio * sum(costs.get(t, mean_cost) for t in task_ids)

    inviolable = _inviolable(task_ids)
    candidates = [t for t in task_ids if t not in inviolable]

    priorities: dict[str, float] = {}
    for task_id in candidates:
        p = estimate_pass_probability(task_id, runs, change, coverage, model)
        c = max(1.0, costs.get(task_id, mean_cost))
        priorities[task_id] = entropy(p) / c

    from agenteval.pts.coverage import candidates as coverage_candidates

    in_scope = coverage_candidates(change, coverage)
    order = sorted(candidates, key=lambda t: (t in in_scope, priorities[t]), reverse=True)
    selected: list[str] = list(inviolable)
    reasons: dict[str, str] = dict.fromkeys(inviolable, "inviolable")
    spent = sum(costs.get(t, mean_cost) for t in inviolable)

    for task_id in order:
        cost = costs.get(task_id, mean_cost)
        if spent + cost > budget_tokens:
            continue
        redundant = any(
            similarity(tool_sequence(runs, task_id), tool_sequence(runs, chosen))
            > redundancy_threshold
            for chosen in selected
            if chosen not in inviolable
        )
        if redundant:
            reasons[task_id] = "redundant"
            continue
        selected.append(task_id)
        reasons[task_id] = "uncertainty"
        spent += cost

    # 予算が余っているなら、冗長として飛ばした候補を優先度順に戻す。
    # （冗長性は「同じ予算でより多様な情報を得る」ための規則であって、
    #   予算が余っているのにテストを実行しない理由にはならない。予算 100% では全件が選ばれる）
    for task_id in order:
        if task_id in selected:
            continue
        cost = costs.get(task_id, mean_cost)
        if spent + cost > budget_tokens:
            continue
        selected.append(task_id)
        reasons[task_id] = "budget_left"
        spent += cost

    # ε-探索: 予算に関係なくランダムに 1 件以上混ぜる（原典 3.8）
    if epsilon > 0:
        rng = np.random.default_rng(seed)
        pool = [t for t in task_ids if t not in selected]
        n_random = max(1, round(epsilon * len(task_ids)))
        for index in rng.permutation(len(pool))[:n_random]:
            task_id = pool[int(index)]
            selected.append(task_id)
            reasons[task_id] = "epsilon"

    skipped = [t for t in task_ids if t not in selected]
    # expected_escape = 「起こりうる不合格のうち、選ばなかったために見逃す割合」。
    # 実測の逃走欠陥率（反転したテストのうち選ばなかった割合）と分母を揃える。
    failure_mass = {
        t: 1 - estimate_pass_probability(t, runs, change, coverage, model) for t in task_ids
    }
    total_mass = sum(failure_mass.values())
    escape = sum(failure_mass[t] for t in skipped) / total_mass if total_mass else 0.0
    return Selection(
        selected=selected,
        skipped=skipped,
        budget=budget_tokens,
        expected_escape=round(escape, 4),
        priorities=priorities,
        reasons=reasons,
    )


def _inviolable(task_ids: list[str]) -> list[str]:
    """不可侵集合（原典 3.8）。選択ロジックの外で必ず実行される。

    ADR-020: 対照（ランダム / 直近失敗優先）にも同じ規則を適用する。
    片方だけに制約が掛かっている比較は対照になっていない。
    """
    return [t for t in task_ids if get_task(t).risk == "inviolable"]


def select_random(
    task_ids: list[str], runs: list[Run], budget_ratio: float = 0.3, seed: int = 0
) -> Selection:
    """対照: 同予算のランダム選択（不可侵集合は必ず含める。ADR-020）。"""
    rng = np.random.default_rng(seed)
    costs = cost_of(runs)
    mean_cost = float(np.mean(list(costs.values()))) if costs else 1.0
    budget_tokens = budget_ratio * sum(costs.get(t, mean_cost) for t in task_ids)
    inviolable = _inviolable(task_ids)
    selected: list[str] = list(inviolable)
    spent = sum(costs.get(t, mean_cost) for t in inviolable)
    order = rng.permutation(len(task_ids))
    for index in order:
        task_id = task_ids[int(index)]
        if task_id in selected:
            continue
        cost = costs.get(task_id, mean_cost)
        if spent + cost > budget_tokens:
            continue
        selected.append(task_id)
        spent += cost
    return Selection(
        selected=selected,
        skipped=[t for t in task_ids if t not in selected],
        budget=budget_tokens,
        expected_escape=0.0,
    )


def select_recent_failures(
    task_ids: list[str], runs: list[Run], budget_ratio: float = 0.3
) -> Selection:
    """対照: 直近失敗優先（不可侵集合は必ず含める。ADR-020）。"""
    costs = cost_of(runs)
    mean_cost = float(np.mean(list(costs.values()))) if costs else 1.0
    budget_tokens = budget_ratio * sum(costs.get(t, mean_cost) for t in task_ids)
    stats = history_stats(runs)
    order = sorted(task_ids, key=lambda t: stats.get(t, (0, 0))[1], reverse=True)
    inviolable = _inviolable(task_ids)
    selected = list(inviolable)
    spent = sum(costs.get(t, mean_cost) for t in inviolable)
    for task_id in order:
        if task_id in selected:
            continue
        cost = costs.get(task_id, mean_cost)
        if spent + cost > budget_tokens:
            continue
        selected.append(task_id)
        spent += cost
    return Selection(
        selected=selected,
        skipped=[t for t in task_ids if t not in selected],
        budget=budget_tokens,
        expected_escape=0.0,
    )
