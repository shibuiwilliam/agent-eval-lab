"""EC-R2 実現可能な pass@k — 「緑になるまで再試行」は静かな誤りを増幅する。

仮説・植込み・対照・基準は `docs/results/EC-R2.md`（正本）と registry.yaml の EC-R2（基準は暫定。W3-A2 が確定する）。
試行プールは EC-R1 と共用する（段 `corpus` の live の run だけ。c01・c13 は 24 タスク × 6 反復、c12 は 24 × 2）。
**run を作らない**。run が欠けていれば案内付きで止まり、予算停止（.partial）の run があれば止まる（PENDING のまま）。

来歴: 試行の S / W / F は live。閉形式の予測・試行プールの並べ替えと再標本化・合成の格子は simulated。
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _coding_common import finalize_coding, json_safe, labeled, nv_array

from agenteval.core.coding_registry import load_coding_tasks
from agenteval.core.coding_store import load_coding_outcome
from agenteval.core.coding_types import CODING_DATA_ROOT, CodingTask
from agenteval.core.schema import Run
from agenteval.econ.outcome import (
    OutcomeClass,
    classify,
    cluster_bootstrap,
    review_diff_lines,
    trials_by_task,
)
from agenteval.econ.retry import (
    closed_form_vs_empirical,
    empirical_by_task,
    identical_trial_rate,
    identity_error_w0,
    realized_closed_form,
    retry_threshold,
)
from agenteval.econ.roi import (
    Theta,
    ThetaSample,
    VersionEconomics,
    economics_of,
    load_theta_space,
    sample_thetas,
)
from agenteval.reports.coding_corpus import load_plan, plan_units, require_stage_runs, unit_states

EXPERIMENT_ID = "EC-R2"
STAGE = "corpus"
BASE, TREATED, NULL = "c01_baseline", "c13_minimal_fix", "c12_null"
VERSIONS = (BASE, TREATED, NULL)
KS_COMPARE = (1, 2, 3, 4)
KS_TABLE = (1, 2, 3, 4, 5, 6)
N_ESTIMATE = 3
N_BOOT = 2000
METHOD_FALLBACK = (
    "段 corpus の live の試行の S / W / F から、可視テストが緑になった最初の試行を採用する再試行方策の成功率と"
    "静かな誤りの率を閉形式と経験値で比べる。詳細は docs/results/EC-R2.md。"
)


def dry_run(seed: int = 20260913) -> dict[str, Any]:  # Any: 表示用
    """`exp --dry-run`: 依存する run の有無と、この実験自身が作る run 数（0）。"""
    del seed
    plan = load_plan()
    units = plan_units(plan, STAGE, versions=VERSIONS)
    states = unit_states(units)
    return {
        "creates_runs": 0,
        "reads": f"段 {STAGE} の live の run（版 {list(VERSIONS)}。EC-R1 と試行プールを共用）だけ",
        "planned": len(units),
        "done": len(states["done"]),
        "partial": len(states["partial"]),
        "missing": len(states["missing"]),
        "ready": not states["partial"] and not states["missing"],
        "how_to_make_runs": "uv run agenteval coding corpus --stage corpus --dry-run（G2 の承認後に --live）",
    }


def final_diff_hashes(
    runs: Sequence[Run], *, data_root: Path = CODING_DATA_ROOT, mode_dir: str = "live"
) -> dict[str, str]:
    """run_id → 最終差分（RepoDiff 全体）の sha256。反復の独立性の診断（identical_trial_rate）用。"""
    out: dict[str, str] = {}
    for run in runs:
        outcome = load_coding_outcome(run.run_id, mode_dir, data_root=data_root)  # type: ignore[arg-type]
        out[run.run_id] = hashlib.sha256(
            outcome.final_diff.model_dump_json().encode("utf-8")
        ).hexdigest()
    return out


def _mean_closed(trials: Mapping[str, Sequence[OutcomeClass]], k: int, field: str) -> float:
    """タスクごとの頻度から閉形式を作り、タスク等重みで平均する。"""
    values: list[float] = []
    for seq in trials.values():
        n = len(seq)
        s, w = seq.count("S") / n, seq.count("W") / n
        cf = realized_closed_form(s, w, 1.0 - s - w, k)
        values.append(getattr(cf, field))
    return float(np.mean(values))


def retry_roi_records(
    e: VersionEconomics, thetas: ThetaSample, *, ks: Sequence[int] = KS_TABLE, stride: int = 100
) -> dict[str, Any]:  # Any: 記録のみの要約
    """Θ 上の最適な k の分布と、「再試行が 1 回目から ROI を下げる」w/s の閾値の分布（記録のみ）。

    NV(k): 可視テストが緑（S か W）になった最初の試行を採用し、最大 k 回。結果の確率は閉形式
    (P_k(S), P_k(W), f^k)、費用は C_a × 期待試行数。版の点推定 (s, w, f) を使う（タスクの異質性は無視）。
    閾値は `retry_threshold`（効用は u_S = −R(Δ_S)、u_W = −(R(Δ_W) + rH' + (1−r)D)、u_F = −(R_F + H')）を
    Θ の `stride` 点おきの部分標本で評価する。
    """
    best = np.stack(
        [
            nv_array(
                e.model_copy(
                    update={
                        "pi_S": (cf := realized_closed_form(e.pi_S, e.pi_W, e.pi_F, k)).p_success,
                        "pi_W": cf.p_silent_wrong,
                        "pi_F": 1.0 - cf.p_success - cf.p_silent_wrong,
                        "cost_usd": e.cost_usd * cf.expected_trials,
                    }
                ),
                thetas,
            )
            for k in ks
        ]
    )
    argmax = np.argmax(best, axis=0)
    dist = {f"k{k}": float(np.mean(argmax == i)) for i, k in enumerate(ks)}

    def utilities(t: Theta) -> tuple[float, float, float]:
        return (
            -(t.R0 + t.rho * e.diff_lines_S),
            -(t.R0 + t.rho * e.diff_lines_W + t.r * t.H_prime + (1.0 - t.r) * t.D),
            -(t.R_F + t.H_prime),
        )

    values = np.array(
        [
            retry_threshold(e.pi_S, t, u=utilities, cost_usd=e.cost_usd)
            for t in list(thetas)[::stride]
        ]
    )
    finite = values[np.isfinite(values)]
    return {
        "best_k_distribution": dist,
        "w_over_s_threshold_quantiles": (
            {
                f"q{int(p * 100):02d}": float(np.quantile(finite, p))
                for p in (0.05, 0.25, 0.5, 0.75, 0.95)
            }
            if finite.size
            else None
        ),
        "w_over_s_threshold_nan_fraction": float(1.0 - finite.size / values.size),
        "w_over_s_observed": e.pi_W / e.pi_S if e.pi_S > 0 else float("nan"),
        "theta_subsample": f"{stride} 点おき（{values.size} 点）",
    }


def _nondecreasing(values: Sequence[float], tol: float = 1e-12) -> bool:
    return all(b >= a - tol for a, b in pairwise(values))


def compute(
    runs_by_version: Mapping[str, Sequence[Run]],
    diff_hash: Mapping[str, str],
    *,
    seed: int,
    n_boot: int = N_BOOT,
    n_estimate: int = N_ESTIMATE,
    ks_compare: Sequence[int] = KS_COMPARE,
    economics: Mapping[str, VersionEconomics] | None = None,
    thetas: ThetaSample | None = None,
) -> dict[str, Any]:  # Any: 指標と補助量
    """指標を計算して {"metrics": …, "extras": …} を返す（run・ファイルを読まない。単体テストできる）。

    `economics`（版 → 版の経済量。EC-R1 と同じ）と `thetas` を渡すと、Θ 上の最適な k と再試行の閾値を記録に足す。
    """
    trials = {v: trials_by_task(runs_by_version[v]) for v in VERSIONS}
    t01, t13 = trials[BASE], trials[TREATED]
    common = sorted(set(t01) & set(t13))
    if not common:
        raise ValueError("c01 と c13 の共通タスクが無い")
    sub01 = {t: t01[t] for t in common}
    sub13 = {t: t13[t] for t in common}

    # --- 閉形式と経験値の比較（前半 n_estimate・後半の分割。k = 4 は復元抽出） ---
    rows = [
        *closed_form_vs_empirical(BASE, sub01, ks_compare, n_estimate=n_estimate, seed=seed),
        *closed_form_vs_empirical(TREATED, sub13, ks_compare, n_estimate=n_estimate, seed=seed),
    ]
    mae_by_version = {
        v: float(
            np.mean([r.abs_error for r in rows if r.version_id == v and r.quantity == "success"])
        )
        for v in (BASE, TREATED)
    }
    closed_form_mae = float(np.mean(list(mae_by_version.values())))

    # --- G_3 の差（6 試行すべてを使う U 統計量。タスク単位で対にしたクラスタ・ブートストラップ） ---
    def g3_diff(tasks: Sequence[str]) -> float:
        return (
            empirical_by_task(sub13, 3, tasks=tasks).gap
            - empirical_by_task(sub01, 3, tasks=tasks).gap
        )

    g3_ci = cluster_bootstrap(common, g3_diff, n_boot=n_boot, seed=seed)

    # --- 再試行で増える静かな誤り（c13） ---
    def sw_gain(tasks: Sequence[str]) -> float:
        return (
            empirical_by_task(sub13, 4, tasks=tasks).p_silent_wrong
            - empirical_by_task(sub13, 1, tasks=tasks).p_silent_wrong
        )

    gain_ci = cluster_bootstrap(common, sw_gain, n_boot=n_boot, seed=seed)
    ks_mono = (1, 2, 3, 4)
    closed_w = [_mean_closed(sub13, k, "p_silent_wrong") for k in ks_mono]
    empirical_w = [empirical_by_task(sub13, k).p_silent_wrong for k in ks_mono]
    monotone = int(_nondecreasing(closed_w) and _nondecreasing(empirical_w))

    # --- 恒等式（自己検査）。版ごとに計算し、最大を取る ---
    ident_errors: list[float] = []
    ident_cases = 0
    for v in VERSIONS:
        ks = [k for k in KS_COMPARE if k <= min(len(s) for s in trials[v].values())]
        err, n_w0 = identity_error_w0(trials[v], ks or [1])
        ident_errors.append(err)
        ident_cases += n_w0

    # --- 記録のみ ---
    table: dict[str, list[dict[str, float]]] = {}
    for v in VERSIONS:
        kmax = min(len(s) for s in trials[v].values())
        table[v] = [
            {
                "k": k,
                "pass_at_k": r.pass_at_k,
                "p_success": r.p_success,
                "p_silent_wrong": r.p_silent_wrong,
                "expected_trials": r.expected_trials,
                "gap": r.gap,
            }
            for k in KS_TABLE
            if k <= kmax
            for r in [empirical_by_task(trials[v], k)]
        ]
    # c12 の試行は 2 回なので G_3 は列挙できない。G_2 の差を記録する（c12 − c01）
    common12 = sorted(set(t01) & set(trials[NULL]))
    sub01_12 = {t: t01[t] for t in common12}
    sub12 = {t: trials[NULL][t] for t in common12}

    def g2_diff(tasks: Sequence[str]) -> float:
        return (
            empirical_by_task(sub12, 2, tasks=tasks).gap
            - empirical_by_task(sub01_12, 2, tasks=tasks).gap
        )

    g2_ci = cluster_bootstrap(common12, g2_diff, n_boot=n_boot, seed=seed)

    all_runs = [r for v in VERSIONS for r in runs_by_version[v]]
    identical = identical_trial_rate(all_runs, diff_hash)
    half = {v: _half_difference(trials[v], n_estimate) for v in (BASE, TREATED)}
    metrics: dict[str, Any] = {
        # --- 基準 ---
        "closed_form_mae_k1to4": labeled(closed_form_mae, "simulated"),
        "g3_diff_ci_low": labeled(g3_ci.low, "simulated"),
        "silent_wrong_monotone_c13": labeled(monotone, "simulated"),
        "silent_wrong_gain_c13_ci_low": labeled(gain_ci.low, "simulated"),
        "identity_error_w0": labeled(max(ident_errors), "simulated"),
        # --- 記録のみ ---
        "closed_form_mae_by_version": labeled(mae_by_version, "simulated"),
        "closed_form_vs_empirical": labeled([r.model_dump() for r in rows], "simulated"),
        "g3_diff": labeled(
            {"estimate": g3_ci.estimate, "low": g3_ci.low, "high": g3_ci.high}, "simulated"
        ),
        "silent_wrong_gain_c13": labeled(
            {"estimate": gain_ci.estimate, "low": gain_ci.low, "high": gain_ci.high}, "simulated"
        ),
        "silent_wrong_closed_form_k1to4_c13": labeled(closed_w, "simulated"),
        "silent_wrong_empirical_k1to4_c13": labeled(empirical_w, "simulated"),
        "g2_diff_c12_minus_c01": labeled(
            {"estimate": g2_ci.estimate, "low": g2_ci.low, "high": g2_ci.high}, "simulated"
        ),
        "realized_table": labeled(table, "simulated"),
        "identity_cases_w0": labeled(ident_cases, "live"),
        "identical_trial_rate": labeled(identical, "live"),
        "first_half_vs_second_half_class_frequency": labeled(half, "live"),
        "n_common_tasks": labeled(len(common), "live"),
    }
    if economics is not None and thetas is not None:
        metrics["retry_roi_over_theta"] = labeled(
            {v: retry_roi_records(economics[v], thetas) for v in (BASE, TREATED)}, "simulated"
        )
    return {"metrics": json_safe(metrics), "extras": {"mae_by_version": mae_by_version}}


def _half_difference(
    trials: Mapping[str, Sequence[OutcomeClass]], n_estimate: int
) -> dict[str, float]:
    """反復の前半と後半の類頻度の差（時間的な漂流の診断。記録のみ）。"""
    out: dict[str, float] = {}
    for cls in ("S", "W", "F"):
        first = [seq[:n_estimate].count(cls) / n_estimate for seq in trials.values()]
        rest = [
            seq[n_estimate:].count(cls) / len(seq[n_estimate:])
            for seq in trials.values()
            if len(seq) > n_estimate
        ]
        out[cls] = float(np.mean(rest) - np.mean(first)) if rest else float("nan")
    return out


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    """段 corpus の run から指標を計算し、結果 JSON と結果ページを書く。`live` は何もしない（run を作らない）。"""
    del live
    runs = require_stage_runs(STAGE, versions=VERSIONS)
    tasks: Mapping[str, CodingTask] = load_coding_tasks()
    by_version: dict[str, list[Run]] = {v: [] for v in VERSIONS}
    for run in runs:
        if run.version_id in by_version and tasks[run.task_id].kind == "bugfix":
            by_version[run.version_id].append(run)
    diff_lines: dict[str, float] = {}
    for rs in by_version.values():
        for run in rs:
            if classify(run) != "F":
                diff_lines[run.run_id] = float(
                    review_diff_lines(load_coding_outcome(run.run_id, "live").final_diff)
                )
    common = sorted(
        set(trials_by_task(by_version[BASE])) & set(trials_by_task(by_version[TREATED]))
    )
    out = compute(
        by_version,
        final_diff_hashes([r for rs in by_version.values() for r in rs]),
        seed=seed,
        economics={
            v: economics_of(v, by_version[v], diff_lines, tasks=common) for v in (BASE, TREATED)
        },
        thetas=sample_thetas(load_theta_space()),  # type: ignore[arg-type]
    )
    metrics = out["metrics"]
    mae = out["extras"]["mae_by_version"]
    notes = [
        (
            "closed_form_mae_k1to4 の閾値 0.05 は仮置きで、推定誤差の床（0.05〜0.07）と同程度のため、閉形式が正しくても"
            "約半分の確率でしか満たされない（EC-R2.md）。不合格は基準設計の問題で、手法の否定的結果としては扱わない。"
            f"版別の MAE: c01 {mae[BASE]:.3f}、c13 {mae[TREATED]:.3f}。"
        ),
        (
            "silent_wrong_monotone_c13 と identity_error_w0 は定義上ほぼ必ず通る自己検査で、H3 の証拠にしない。"
            "H3 の量的な主張は silent_wrong_gain_c13_ci_low（再試行で増える W の量）で検査している。"
        ),
        (
            "反復の独立性の診断: identical_trial_rate = "
            f"{metrics['identical_trial_rate']['value']}（塩が効いていれば 0 に近い。高ければ F1 の実装を疑う。"
            "EC-R2.md では塩の不備で反復が同一になれば FAIL）。"
        ),
        (
            "c12（試行 2 回）では G_3 が列挙できないので、c12 と c01 の差は G_2 で記録した（g2_diff_c12_minus_c01、"
            "記録のみ）。k = 5・6 の表は 6 試行ある版（c01・c13）だけに出す。"
        ),
        (
            "閉形式は「タスク内で試行が交換可能」という仮定の代数で、経験値は同じ試行プールからの再標本化である。"
            "比較は推定量どうしの偏りの差を見るもので、仮定が破れる典型（カセットの衝突で反復が同一になる）を"
            "前半・後半の分割では検出できない。主張の重みは g3_diff_ci_low と silent_wrong_gain_c13_ci_low に置く。"
        ),
    ]
    return finalize_coding(
        EXPERIMENT_ID,
        metrics,
        notes,
        seed=seed,
        provenance="live",
        method_fallback=METHOD_FALLBACK,
        # 自己検査（恒等式・単調性）の未達は実装の誤り（FAIL）。それ以外は手法が主張どおり働かなかった（NEGATIVE）
        negative_if_only=(
            "closed_form_mae_k1to4",
            "g3_diff_ci_low",
            "silent_wrong_gain_c13_ci_low",
        ),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
