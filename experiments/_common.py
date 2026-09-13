"""実験スクリプトの共通処理。

- レジストリから 5 項目を読む（カタログと同じものを 2 か所に書かない）
- 判定は criteria だけから導く（`reports.pages.check_criterion`）
- 数値には必ず来歴ラベルを付ける（ADR-004）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agenteval.core.schema import Run
from agenteval.core.store import load_corpus
from agenteval.reports import pages
from agenteval.reports.verify import load_registry


def meta_of(experiment_id: str) -> dict[str, Any]:
    """レジストリから 1 実験のメタデータを取る。"""
    for entry in load_registry():
        if entry["id"] == experiment_id:
            return entry
    raise KeyError(f"registry.yaml に無い実験: {experiment_id}")


def labeled(value: Any, provenance: str = "simulated") -> dict[str, Any]:
    """数値に来歴ラベルを付ける。"""
    return {"value": value, "provenance": provenance}


def corpus() -> list[Run]:
    """コーパスを読む。無ければ corpus build を促す。"""
    runs = load_corpus()
    if not runs:
        raise RuntimeError(
            "コーパスが空です。先に `uv run agenteval corpus build --plan corpus.yaml --sim` を実行してください"
        )
    return runs


def runs_of(runs: list[Run], version_id: str) -> list[Run]:
    return [r for r in runs if r.version_id == version_id]


def pass_map(runs: list[Run], version_id: str) -> dict[str, float]:
    """タスク ID → 合格率（指定版）。"""
    per: dict[str, list[bool]] = {}
    for run in runs:
        if run.version_id == version_id:
            per.setdefault(run.task_id, []).append(run.passed())
    return {task_id: sum(v) / len(v) for task_id, v in per.items()}


def flake_band(runs: list[Run], base: str = "v01_baseline") -> dict[str, float]:
    """タスクごとのフレーク帯。

    ベースライン版の反復から二項の揺れ幅（Wald の 95% 半幅）を取る。
    この幅の内側の合格率の差は「変化」と数えない（02-experiment-catalog.md の定義）。
    """
    import math

    per: dict[str, list[bool]] = {}
    for run in runs:
        if run.version_id == base:
            per.setdefault(run.task_id, []).append(run.passed())
    out: dict[str, float] = {}
    for task_id, outcomes in per.items():
        n = len(outcomes)
        p = sum(outcomes) / n
        out[task_id] = round(1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n), 4)
    return out


def paired_outcomes(runs: list[Run], version_id: str) -> dict[tuple[str, int], bool]:
    """(task_id, repeat) → 合否。版をまたいで同じ鍵を突き合わせる（対応のある比較）。"""
    return {(r.task_id, r.repeat): r.passed() for r in runs if r.version_id == version_id}


def disagreement(runs: list[Run], base: str, target: str) -> dict[str, float]:
    """タスクごとの「同じ反復で合否が変わった割合」。"""
    a, b = paired_outcomes(runs, base), paired_outcomes(runs, target)
    per: dict[str, list[bool]] = {}
    for key, passed in a.items():
        if key in b:
            per.setdefault(key[0], []).append(passed != b[key])
    return {task_id: sum(v) / len(v) for task_id, v in per.items()}


def flipped_tasks(
    runs: list[Run], base: str, target: str, bands: dict[str, float] | None = None
) -> set[str]:
    """base → target で反転したタスク（ADR-009 の定義）。

    反転 = **同じ (task, repeat) の対**で合否が変わった割合が、そのタスクのフレーク帯
    （ベースライン版の反復から出した二項の 95% 半幅）を超えること。
    対応のある比較にすることで、反復ごとの揺れ（同じ seed / repeat から引く「運」）が相殺される。
    """
    band = bands if bands is not None else flake_band(runs, base)
    diff = disagreement(runs, base, target)
    return {task_id for task_id, rate in diff.items() if rate > band.get(task_id, 0.0)}


def flaky_tasks(runs: list[Run], version_id: str) -> set[str]:
    """反復で合否が割れたタスク（フレーク帯の中身）。"""
    per: dict[str, list[bool]] = {}
    for run in runs:
        if run.version_id == version_id:
            per.setdefault(run.task_id, []).append(run.passed())
    return {t for t, v in per.items() if 0 < sum(v) < len(v)}


def verdict_from(
    meta: dict[str, Any], metrics: dict[str, Any], failure_type: str = "fail"
) -> tuple[str, str]:
    """criteria だけから判定を導く。"""
    rows = []
    for criterion in meta.get("criteria", []):
        value = metrics.get(criterion["metric"])
        ok = value is not None and pages.check_criterion(value, criterion)
        rows.append((criterion, ok))
    if all(ok for _, ok in rows) and rows:
        return "PASS", "全基準を満たした。"
    failed = [c["metric"] for c, ok in rows if not ok]
    verdict = "NEGATIVE" if failure_type == "negative" else "FAIL"
    return verdict, f"未達の基準: {', '.join(failed)}。"


def finalize(
    experiment_id: str,
    metrics: dict[str, Any],
    method: str,
    notes: list[str],
    figures: list[tuple[str, str]] | None = None,
    seed: int = 20260913,
    provenance: str = "simulated",
    usd: float = 0.0,
    failure_type: str = "fail",
    verdict_reason_extra: str = "",
) -> dict[str, Any]:
    """結果 JSON を組み立て、結果ページを書き出す。"""
    meta = meta_of(experiment_id)
    verdict, reason = verdict_from(meta, metrics, failure_type)
    result = {
        "id": experiment_id,
        "ran_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "provenance": provenance,
        "metrics": metrics,
        "figures": figures or [],
        "notes": notes,
        "method": method,
        "verdict": verdict,
        "verdict_reason": reason + (" " + verdict_reason_extra if verdict_reason_extra else ""),
        "failure_type": failure_type,
        "usd": usd,
    }
    pages.write(meta, result)
    return result
