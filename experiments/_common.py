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
    """sim コーパスを読む。無ければ corpus build を促す。

    `data/traces/` には live 実行（E0-1 の比較用）も入るので、**mode で必ず絞る**。
    絞らないと live の run が sim コーパスの統計に混ざる（live 検証で見つかった不具合）。
    """
    runs = [r for r in load_corpus() if r.mode == "sim"]
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
    """base → target で反転したタスク（ADR-009 ＋ ADR-016）。

    反転 = **同じ (task, repeat) の対**で合否が変わったものが 1 件でもあること。

    ADR-016: sim は `(task_id, seed, repeat)` に対して決定的で、版をまたいで同じ運を使う。
    したがって「版に効果が無い」という帰無仮説の下での不一致は**厳密に 0** であり、
    閾値を置く根拠が無い。改訂前は周辺合格率の Wald 半幅を不一致率の閾値に流用しており、
    合格率 0.5 付近のタスクで 0.31 という大きな閾値を課して実在する反転を取り逃していた
    （v09 で 23 件中 19 件、v06 で 11 件中 5 件）。

    `bands` を明示的に渡した場合はその閾値を使う（改訂前の値を出すための経路）。
    live のように同じ条件でも応答が揺れる場合は `mcnemar_flipped()` を使う。
    """
    diff = disagreement(runs, base, target)
    if bands is None:
        return {task_id for task_id, rate in diff.items() if rate > 0.0}
    return {task_id for task_id, rate in diff.items() if rate > bands.get(task_id, 0.0)}


def mcnemar_flipped(runs: list[Run], base: str, target: str, alpha: float = 0.05) -> set[str]:
    """live 用の反転判定: 対応のある不一致に McNemar 正確検定をかける。

    sim では帰無仮説下の不一致が 0 なので使わない（`flipped_tasks` を使う）。
    live で同じ条件でも応答が揺れる場合、不一致の向きが偏っているかを二項検定で見る。
    """
    from scipy.stats import binomtest

    a, b = paired_outcomes(runs, base), paired_outcomes(runs, target)
    discordant: dict[str, list[int]] = {}
    for key, passed in a.items():
        if key not in b or passed == b[key]:
            continue
        # +1 = base 合格 → target 不合格（退行）、-1 = その逆
        discordant.setdefault(key[0], []).append(1 if passed else -1)
    out = set()
    for task_id, signs in discordant.items():
        n = len(signs)
        k = sum(1 for s in signs if s > 0)
        if binomtest(k, n, 0.5).pvalue < alpha:
            out.add(task_id)
    return out


def failures_by_repeat(runs: list[Run], version_id: str) -> dict[int, set[str]]:
    """反復ごとの「フル実行で見つかった失敗」`F_full`（原典 3.8、ADR-018）。

    フル実行 1 回 = 各テストを 1 回ずつ走らせること。反復 r のフル実行で不合格になった
    タスク集合が `F_full(r)` である。逃走欠陥率は反復ごとに出して平均する
    （「過半数で落ちるか」で集約すると、合格率 0.6〜0.7 のタスクが失敗に数えられず
    分母がほとんど空になる）。
    """
    out: dict[int, set[str]] = {}
    for run in runs:
        if run.version_id == version_id and not run.passed():
            out.setdefault(run.repeat, set()).add(run.task_id)
    for run in runs:
        if run.version_id == version_id:
            out.setdefault(run.repeat, set())
    return out


def escape_over_failures(selected: set[str], failures: dict[int, set[str]]) -> float:
    """`Escape(S) = |F_full \\ S| / |F_full|` を反復ごとに出して平均する。"""
    import numpy as np

    rates = [len(fails - selected) / len(fails) for fails in failures.values() if fails]
    return round(float(np.mean(rates)), 4) if rates else 0.0


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
