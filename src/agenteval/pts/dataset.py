"""PTS の学習データ（変更 × テスト → 回帰したか）。

原典 3.4 の特徴量表をそのまま実装し、産業用 PTS（変更とテストの交差特徴）で補う。

**漏洩を作らないための規律**
- 特徴量は「ベースラインの履歴」と「変更のメタデータ」からだけ作る。
  対象版の run は**ラベルにしか使わない**
- 過去の変更のラベルを使う特徴量（種類別の故障率など）は、交差検証の
  **訓練フォールドだけ**から計算する（`history_features` を fold ごとに呼ぶ）
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agenteval.core.registry import get_task
from agenteval.core.schema import Change, Run
from agenteval.pts.coverage import change_units

CHANGE_KINDS = ("prompt", "tool", "config", "model", "fixture", "code")
TASK_CATEGORIES = ("scheduling", "mail", "files", "mixed", "edge")
TASK_KINDS = ("normal", "do_nothing", "ambiguous", "niah", "trivial")


@dataclass
class BaselineStats:
    """ベースライン版の履歴から作る、変更に依存しないテスト側の統計。"""

    pass_rate: dict[str, float] = field(default_factory=dict)
    flake_rate: dict[str, float] = field(default_factory=dict)
    steps: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, float] = field(default_factory=dict)
    n_runs: dict[str, int] = field(default_factory=dict)
    margin: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, runs: list[Run], baseline: str = "v01_baseline") -> BaselineStats:
        from agenteval.pts.coverage import coverage_map

        subset = [r for r in runs if r.version_id == baseline]
        per: dict[str, list[Run]] = defaultdict(list)
        for run in subset:
            per[run.task_id].append(run)
        stats = cls(coverage=coverage_map(subset))
        for task_id, group in per.items():
            outcomes = [r.passed() for r in group]
            n = len(outcomes)
            rate = sum(outcomes) / n
            stats.pass_rate[task_id] = rate
            stats.flake_rate[task_id] = 1.0 if 0 < sum(outcomes) < n else 0.0
            stats.steps[task_id] = sum(r.n_steps for r in group) / n
            stats.tokens[task_id] = sum(r.cost.total_tokens for r in group) / n
            stats.n_runs[task_id] = n
            # スコア余裕: 部分点が合格閾値からどれだけ離れているか（原典 4.5 の milestone_score）
            scores = [r.outcome.milestone_score if r.outcome else 0.0 for r in group]
            stats.margin[task_id] = sum(scores) / n
        return stats


@dataclass
class Row:
    """学習データ 1 行 = (変更, テスト)。"""

    change_id: str
    family: str
    task_id: str
    seq: int
    units: set[str]
    label_regressed: int
    label_failed: int
    cost: float
    static: dict[str, float]

    def vector(self, dynamic: dict[str, float]) -> list[float]:
        merged = {**self.static, **dynamic}
        return [merged[k] for k in sorted(merged)]

    def names(self, dynamic: dict[str, float]) -> list[str]:
        return sorted({**self.static, **dynamic})


def static_features(
    task_id: str, change: Change, stats: BaselineStats, version: Any | None = None
) -> dict[str, float]:
    """変更に依存する分を含む、ラベルを使わない特徴量。

    原典 3.4 の表の 5 つ（直近の合格率 / 反転回数 / スコア余裕 / カバレッジ重なり / フレーク率）と、
    産業用 PTS が使う交差特徴（変更が触る要素とテストが触る要素の重なり方）を入れる。
    """
    task = get_task(task_id)
    used = stats.coverage.get(task_id, set())
    target = change_units(change)
    overlap = used & target
    tools_used = {u for u in used if u.startswith("tool:")}
    sections_used = {u for u in used if u.startswith("section:")}

    out: dict[str, float] = {
        # --- 原典 3.4 の表 ---
        "base_pass_rate": stats.pass_rate.get(task_id, 0.5),
        "base_flake": stats.flake_rate.get(task_id, 0.0),
        "score_margin": stats.margin.get(task_id, 0.0),
        "coverage_overlap": float(len(overlap)),
        # --- 交差特徴（変更 × テスト） ---
        "overlap_ratio": len(overlap) / max(1, len(used)),
        "overlap_of_change": len(overlap) / max(1, len(target)) if target else 0.0,
        "touches_change": 1.0 if overlap else 0.0,
        "change_is_global": 1.0 if not target else 0.0,
        # --- テスト側 ---
        "n_tools": float(len(tools_used)),
        "n_sections": float(len(sections_used)),
        "base_steps": stats.steps.get(task_id, 0.0),
        "log_tokens": math.log1p(stats.tokens.get(task_id, 0.0)),
        "l_min": float(task.l_min),
        "steps_over_lmin": stats.steps.get(task_id, 0.0) / max(1, task.l_min),
        "is_inviolable": 1.0 if task.risk == "inviolable" else 0.0,
        # --- 変更側 ---
        "n_components": float(len(change.components)),
    }

    # --- 設定変更の「効き具合」を変更 × テストの交差特徴として入れる ---
    # これが無いと `max_steps=3` と `max_steps=6` の特徴ベクトルが完全に一致してしまい、
    # 設定変更はモデルから見て区別できない（＝原理的に学習できない）。
    # 産業用 PTS の「変更した要素とテストの距離」に相当する特徴である。
    base_steps = stats.steps.get(task_id, 0.0)
    config = getattr(version, "config", None)
    max_steps = float(getattr(config, "max_steps", 25) or 25)
    summarize_after = float(getattr(config, "summarize_after", 3) or 3)
    strategy = getattr(config, "context_strategy", "raw")
    out.update(
        {
            "max_steps": max_steps,
            "max_steps_headroom": max_steps - base_steps,
            "max_steps_tight": 1.0 if max_steps - base_steps < 1.0 else 0.0,
            "summarize_active": 1.0 if strategy == "summarize" else 0.0,
            "summarize_headroom": (summarize_after - base_steps)
            if strategy == "summarize"
            else 99.0,
            "plan_first": 1.0 if getattr(config, "plan_first", False) else 0.0,
            "model_changed": 1.0 if change.kind == "model" else 0.0,
        }
    )
    for kind in CHANGE_KINDS:
        out[f"change_kind_{kind}"] = 1.0 if change.kind == kind else 0.0
    for category in TASK_CATEGORIES:
        out[f"task_cat_{category}"] = 1.0 if task.category == category else 0.0
    for kind in TASK_KINDS:
        out[f"task_kind_{kind}"] = 1.0 if task.kind == kind else 0.0
    return out


def history_features(history: Any, row: Row) -> dict[str, float]:
    """変更履歴・テスト履歴から作るラグ／交差特徴（`pts/history.py`）。

    `history` には**その行より前の変更しか入っていない**か、あるいは
    `History.features` が `seq` で切るので、未来の記録は参照されない。
    交差検証では訓練フォールドだけから台帳を組み立てる。
    """
    features: dict[str, float] = history.features(row.seq, row.task_id, row.units)
    return features


def build_rows(
    runs: list[Run],
    changes: list[Any],
    baseline: str = "v01_baseline",
) -> tuple[list[Row], BaselineStats]:
    """(変更, テスト) の行を作る。

    ラベル:
      - `label_regressed` = ベースラインで合格した反復と同じ反復で、対象版が不合格になった
        （対応のある比較。ADR-016 と同じ規律で、1 件でもあれば 1）
      - `label_failed` = 対象版の反復の過半で不合格
    """
    stats = BaselineStats.build(runs, baseline)
    base_outcome: dict[tuple[str, int], bool] = {
        (r.task_id, r.repeat): r.passed() for r in runs if r.version_id == baseline
    }
    by_version: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_version[run.version_id].append(run)

    rows: list[Row] = []
    for change in changes:
        target_runs = by_version.get(change.id, [])
        if not target_runs:
            continue
        per_task: dict[str, list[Run]] = defaultdict(list)
        for run in target_runs:
            per_task[run.task_id].append(run)
        ch = change.change()
        churn = _churn_features(getattr(change, "churn", {}) or {})
        for task_id, group in per_task.items():
            regressed = any(
                base_outcome.get((task_id, r.repeat), False) and not r.passed() for r in group
            )
            failed = sum(1 for r in group if not r.passed()) > len(group) / 2
            static = static_features(task_id, ch, stats, getattr(change, "version", None))
            static.update(churn)
            rows.append(
                Row(
                    change_id=change.id,
                    family=change.family,
                    task_id=task_id,
                    seq=int(getattr(change, "seq", 0)),
                    units=set(ch.components),
                    label_regressed=int(regressed),
                    label_failed=int(failed),
                    cost=stats.tokens.get(task_id, 1.0),
                    static=static,
                )
            )
    return add_within_change_ranks(rows), stats


def _churn_features(churn: dict[str, float]) -> dict[str, float]:
    """変更量（行数・文字数・要素数）。産業用 PTS の change size 特徴。"""
    out: dict[str, float] = {}
    for key in ("lines_added", "lines_removed", "lines_changed", "chars_delta", "n_units"):
        value = float(churn.get(key, 0.0))
        out[f"churn_{key}"] = value
        out[f"churn_log_{key}"] = math.log1p(value)
    out["churn_is_pure_config"] = 1.0 if churn.get("lines_changed", 0.0) == 0.0 else 0.0
    return out


# 変更の中で値が変わる連続特徴。これらの「変更内での順位」を足す（ADR-027）。
RANKED_FEATURES = (
    "coverage_overlap",
    "overlap_ratio",
    "overlap_of_change",
    "base_pass_rate",
    "base_steps",
    "steps_over_lmin",
    "max_steps_headroom",
    "summarize_headroom",
    "n_tools",
)


def add_within_change_ranks(rows: list[Row]) -> list[Row]:
    """変更ごとに、連続特徴の百分位順位を特徴量として足す（ADR-027）。

    テスト選択は**変更ごとの順位付け**なので、変更内で一定の特徴量（`change_kind_*` など）は
    順位に寄与できない。相対位置を明示的に渡すことで、点推定の分類器でも
    変更内の順位を学習できるようにする。
    """
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row.change_id].append(row)
    for group in groups.values():
        n = len(group)
        for name in RANKED_FEATURES:
            values = [r.static.get(name, 0.0) for r in group]
            order = sorted(range(n), key=lambda i: values[i])
            ranks = [0.0] * n
            for position, index in enumerate(order):
                ranks[index] = position / max(1, n - 1)
            spread = max(values) - min(values)
            for row, rank, value in zip(group, ranks, values, strict=True):
                row.static[f"{name}_rank_in_change"] = round(rank, 4)
                row.static[f"{name}_z_in_change"] = (
                    round((value - min(values)) / spread, 4) if spread else 0.0
                )
    return rows


def regressions_by_repeat(
    runs: list[Run], change_id: str, baseline: str = "v01_baseline"
) -> dict[int, set[str]]:
    """反復ごとの「新たに落ちたテスト」集合。逃走欠陥率の分母に使う。"""
    base_outcome: dict[tuple[str, int], bool] = {
        (r.task_id, r.repeat): r.passed() for r in runs if r.version_id == baseline
    }
    out: dict[int, set[str]] = defaultdict(set)
    for run in runs:
        if run.version_id != change_id:
            continue
        out.setdefault(run.repeat, set())
        if base_outcome.get((run.task_id, run.repeat), False) and not run.passed():
            out[run.repeat].add(run.task_id)
    return dict(out)
