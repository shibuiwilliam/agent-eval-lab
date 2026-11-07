"""EC-R1 3 類分解と決定ロバストな ROI（核）。

仮説・植込み・対照・基準は `docs/results/EC-R1.md`（正本）と registry.yaml の EC-R1（基準は暫定。W3-A2 が確定する）。
入力は段 `corpus` の live の run だけ（パイロット・操作確認の run を混ぜない。ADR-033 判断 5）。
**run を作らない**。run が欠けていれば案内付きで止まり、予算停止（.partial）の run があれば止まる（PENDING のまま）。

来歴: S / W / F・類確率・1 run の費用・差分の行数・ブートストラップ区間は live。Θ の掃引と Rob は simulated
（live の点推定を入力にした計算）。表では列を分ける。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _coding_common import (
    breakeven_summary,
    conditional_rob,
    failure_caution,
    finalize_coding,
    json_safe,
    labeled,
    nv_array,
)

from agenteval.core.coding_registry import load_coding_tasks
from agenteval.core.coding_store import load_coding_outcome
from agenteval.core.coding_types import ACC_HIDDEN, ACC_VISIBLE, CODING_DATA_ROOT, CodingTask
from agenteval.core.schema import Run
from agenteval.econ.outcome import (
    classify,
    cluster_bootstrap,
    review_diff_lines,
    trials_by_task,
)
from agenteval.econ.roi import (
    ThetaSample,
    ThetaSpace,
    apply_variant,
    confident_fraction,
    economics_of,
    load_theta_space,
    paired_economics_bootstrap,
    rank_reversal,
    robustness,
    sample_thetas,
)
from agenteval.reports.coding_corpus import (
    StageIncompleteError,
    load_plan,
    plan_units,
    require_stage_runs,
    unit_states,
)

EXPERIMENT_ID = "EC-R1"
STAGE = "corpus"
BASE, TREATED, NULL = "c01_baseline", "c13_minimal_fix", "c12_null"
VERSIONS = (BASE, TREATED, NULL)
N_BOOT = 2000
METHOD_FALLBACK = "段 corpus の live の run の S / W / F から版の経済量を作り、Θ 上の Rob と同時信頼帯で決定の頑健さを測る。詳細は docs/results/EC-R1.md。"


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


def dry_run(seed: int = 20260913) -> dict[str, Any]:  # Any: 表示用
    """`exp --dry-run`: 依存する run の有無と、この実験自身が作る run 数（0）。"""
    del seed
    plan = load_plan()
    units = plan_units(plan, STAGE, versions=VERSIONS)
    states = unit_states(units)
    return {
        "creates_runs": 0,
        "reads": f"段 {STAGE} の live の run（版 {list(VERSIONS)}）だけ",
        "planned": len(units),
        "done": len(states["done"]),
        "partial": len(states["partial"]),
        "missing": len(states["missing"]),
        "ready": not states["partial"] and not states["missing"],
        "how_to_make_runs": "uv run agenteval coding corpus --stage corpus --dry-run（G2 の承認後に --live）",
    }


def diff_lines_of(
    runs: Sequence[Run], *, data_root: Path = CODING_DATA_ROOT, mode_dir: str = "live"
) -> dict[str, float]:
    """S / W の run の差分行数（レビュー費用）。F の run は経済量が差分を使わないので読まない。"""
    out: dict[str, float] = {}
    for run in runs:
        if classify(run) == "F":
            continue
        outcome = load_coding_outcome(run.run_id, mode_dir, data_root=data_root)  # type: ignore[arg-type]
        out[run.run_id] = float(review_diff_lines(outcome.final_diff))
    return out


def _by_version(runs: Sequence[Run], tasks: Mapping[str, CodingTask]) -> dict[str, list[Run]]:
    """bugfix のタスクの run だけを版ごとに（`do_nothing` / `ambiguous` は 3 類に当てはまらない）。"""
    out: dict[str, list[Run]] = {v: [] for v in VERSIONS}
    for run in runs:
        if run.version_id in out and tasks[run.task_id].kind == "bugfix":
            out[run.version_id].append(run)
    return out


def _task_rates(runs: Sequence[Run]) -> dict[str, tuple[float, float]]:
    """タスク → (W 率, 可視合格率 = S 率 + W 率)。"""
    out: dict[str, tuple[float, float]] = {}
    for task, classes in trials_by_task(runs).items():
        n = len(classes)
        out[task] = (classes.count("W") / n, (classes.count("S") + classes.count("W")) / n)
    return out


# ---------------------------------------------------------------------------
# 計算（run を読まない。単体テストできる）
# ---------------------------------------------------------------------------


def compute(
    by_version: Mapping[str, Sequence[Run]],
    diff_lines: Mapping[str, float],
    mutation_scores: Mapping[str, float],
    test_files_modified: Mapping[str, int],
    *,
    space: ThetaSpace,
    seed: int,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:  # Any: 指標と補助量
    """指標を計算して {"metrics": …, "extras": …} を返す。

    点推定と複製のタスク集合は、比べる 2 版の**共通タスク**を `tasks=` で明示して揃える
    （片方にだけ run があるタスクが点推定に混ざらないように。W0-Z）。
    """
    r01, r13, r12 = by_version[BASE], by_version[TREATED], by_version[NULL]
    t01, t13, t12 = set(_task_rates(r01)), set(_task_rates(r13)), set(_task_rates(r12))
    common13 = sorted(t01 & t13)
    common12 = sorted(t01 & t12)
    if not common13 or not common12:
        raise ValueError("c01 と c13、c01 と c12 の共通タスクが無い")
    thetas = sample_thetas(space)
    assert isinstance(thetas, ThetaSample)

    # --- 点推定（共通タスクで揃える） ---
    e01_13 = economics_of(BASE, r01, diff_lines, tasks=common13)
    e13 = economics_of(TREATED, r13, diff_lines, tasks=common13)
    e01_12 = economics_of(BASE, r01, diff_lines, tasks=common12)
    e12 = economics_of(NULL, r12, diff_lines, tasks=common12)
    rob_13 = robustness(e01_13, e13, thetas)
    rob_12 = robustness(e12, e01_12, thetas)

    # --- W 率・可視合格率の差（タスク単位で対にしたクラスタ・ブートストラップ） ---
    rates01, rates13 = _task_rates(r01), _task_rates(r13)

    def w_diff(tasks: Sequence[str]) -> float:
        return float(np.mean([rates13[t][0] - rates01[t][0] for t in tasks]))

    def vis_diff(tasks: Sequence[str]) -> float:
        return float(np.mean([rates13[t][1] - rates01[t][1] for t in tasks]))

    w_ci = cluster_bootstrap(common13, w_diff, n_boot=n_boot, seed=seed)
    vis_ci = cluster_bootstrap(common13, vis_diff, n_boot=n_boot, seed=seed)

    # --- 決定のロバスト性の不確かさ（対にしたタスク複製） ---
    rep13 = paired_economics_bootstrap(r01, r13, diff_lines, n_boot=n_boot, seed=seed)
    rep12 = paired_economics_bootstrap(r01, r12, diff_lines, n_boot=n_boot, seed=seed)
    boot_rob = np.array([robustness(a, b, thetas) for a, b in rep13])
    conf_13 = confident_fraction(rep13, (e01_13, e13), thetas, direction="a_over_b")
    conf_12 = confident_fraction(rep12, (e01_12, e12), thetas, direction="either")

    # --- 記録のみ ---
    variants: dict[str, float] = {}
    for name in space.variants:
        variants[name] = robustness(e01_13, e13, sample_thetas(apply_variant(space, name)))
    nv_diff_13 = nv_array(e01_13, thetas) - nv_array(e13, thetas)
    all_runs = [r for rs in by_version.values() for r in rs]
    vis_fail_hidden_pass = sum(
        1
        for r in all_runs
        if r.outcome is not None
        and not r.outcome.acceptance_results[ACC_VISIBLE]
        and r.outcome.acceptance_results[ACC_HIDDEN]
    )
    pooled = {
        v: {
            "S": sum(classify(r) == "S" for r in by_version[v]),
            "W": sum(classify(r) == "W" for r in by_version[v]),
            "F": sum(classify(r) == "F" for r in by_version[v]),
        }
        for v in VERSIONS
    }
    metrics: dict[str, Any] = {
        # --- 基準 ---
        "w_rate_diff_ci_low": labeled(w_ci.low, "live"),
        "visible_vs_nv_rank_reversal": labeled(rank_reversal(vis_ci.high, rob_13), "simulated"),
        "rob_c01_over_c13": labeled(rob_13, "simulated"),
        "null_confident_fraction_c12": labeled(conf_12, "simulated"),
        # --- 記録のみ ---
        "rob_c12_vs_c01": labeled(rob_12, "simulated"),
        "confident_fraction_c01_over_c13": labeled(conf_13, "simulated"),
        "rob_c01_over_c13_boot_q05": labeled(float(np.quantile(boot_rob, 0.05)), "simulated"),
        "w_rate_diff": labeled(
            {"estimate": w_ci.estimate, "low": w_ci.low, "high": w_ci.high}, "live"
        ),
        "visible_rate_diff": labeled(
            {"estimate": vis_ci.estimate, "low": vis_ci.low, "high": vis_ci.high}, "live"
        ),
        "class_counts": labeled(pooled, "live"),
        "economics": labeled(
            {
                BASE: e01_13.model_dump(),
                TREATED: e13.model_dump(),
                f"{BASE}_for_{NULL}": e01_12.model_dump(),
                NULL: e12.model_dump(),
            },
            "live",
        ),
        "breakeven_pi_s": labeled(
            {BASE: breakeven_summary(e01_13, thetas), TREATED: breakeven_summary(e13, thetas)},
            "simulated",
        ),
        "rob_by_theta_variant": labeled(variants, "simulated"),
        "rob_conditional_on_parameter_tercile": labeled(
            conditional_rob(nv_diff_13, thetas), "simulated"
        ),
        "vis_fail_hidden_pass": labeled(vis_fail_hidden_pass, "live"),
        "test_files_modified": labeled(dict(test_files_modified), "live"),
        # タスク生成時のメタデータ（run の観測ではない）。来歴は ADR-004 の 3 種のうち、より保守的な simulated を付ける
        "hidden_test_mutation_score_mean": labeled(
            float(np.mean([mutation_scores[t] for t in common13])), "simulated"
        ),
        "hidden_test_mutation_score_min": labeled(
            float(min(mutation_scores[t] for t in common13)), "simulated"
        ),
        "n_common_tasks": labeled({"c01_c13": len(common13), "c01_c12": len(common12)}, "live"),
        "n_theta": labeled(space.n_samples, "simulated"),
    }
    extras = {
        "w_ci": w_ci.model_dump(),
        "vis_ci": vis_ci.model_dump(),
        "n_boot": n_boot,
        "n_common": (len(common13), len(common12)),
    }
    return {"metrics": json_safe(metrics), "extras": extras}


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    """段 corpus の run から指標を計算し、結果 JSON と結果ページを書く。`live` は何もしない（run を作らない）。"""
    del live
    runs = require_stage_runs(STAGE, versions=VERSIONS)
    tasks = load_coding_tasks()
    by_version = _by_version(runs, tasks)
    diff_lines = diff_lines_of([r for rs in by_version.values() for r in rs])
    mutation_scores = {
        tid: float(t.generation.mutation_score)
        for tid, t in tasks.items()
        if t.generation is not None
    }
    modified = {
        v: sum(
            1 for r in by_version[v] if load_coding_outcome(r.run_id, "live").test_files_modified
        )
        for v in VERSIONS
    }
    out = compute(
        by_version, diff_lines, mutation_scores, modified, space=load_theta_space(), seed=seed
    )
    metrics = out["metrics"]
    notes = [
        (
            "EC-R1.md の「気づき・限界」への申し送り: 隠しテストは「バンク − K ＋ h 本」で、K の一部だけを直す"
            "部分的な修正は S に数えられうる（W0-Z の変異スコアの平均は 0.84）。S の中に本当は不完全な修正が"
            f"混ざりうるので W 率は下限寄りである。この実験の 24 タスクの変異スコア平均 "
            f"{metrics['hidden_test_mutation_score_mean']['value']:.3f}、最小 {metrics['hidden_test_mutation_score_min']['value']:.3f}。"
            "変異スコアはタスク生成時のメタデータ（run の観測ではない）なので、来歴は simulated を付けた。"
        ),
        (
            "Rob は点推定の差の向きだけで決まり、真の差が 0 でも 0 か 1 に張り付く。対照 c12 では rob_c12_vs_c01 "
            f"= {metrics['rob_c12_vs_c01']['value']:.3f}。確信できる θ の割合 null_confident_fraction_c12 "
            f"= {metrics['null_confident_fraction_c12']['value']:.3f}（同時信頼帯は名目 95%。24 クラスタでの実被覆は約 0.85）。"
            "visible_vs_nv_rank_reversal は live（可視合格率の差の区間）と simulated（Θ 上の Rob）を組み合わせた指標で、"
            "来歴ラベルは ADR-004 の 3 種のうちより保守的な simulated を付けた。"
        ),
        (
            "Rob の到達上限は c13 の W の出どころで決まる（純粋な F→W で 0.887、S からの移動が増えるほど 1 に近づく。"
            "EC-R1.md）。rob_c01_over_c13 が 0.9 に届かないのは、上限の範囲内なら計測の失敗ではなく"
            "「W と F の交換は仮定の約 11% の範囲で損得が逆転する」という決定の仮定依存の結果である。"
        ),
        (
            "損益分岐の π_S* の nan は「常に得」「常に損」「定義なし」に分けて breakeven_pi_s に記録した。"
            "分位点は定義のある θ だけで計算している。"
        ),
        (
            "テスト改変（test_files_modified）は記録のみで、評価は元のテストファイルで行った。vis_fail_hidden_pass は"
            "可視不合格かつ隠し合格の run の数（隠しテストが可視テストを含む設計なら 0 のはず）。"
        ),
    ]
    return finalize_coding(
        EXPERIMENT_ID,
        metrics,
        notes,
        seed=seed,
        provenance="live",
        method_fallback=METHOD_FALLBACK,
        # 自動の判定は FAIL。NEGATIVE は designer が --failure-type negative --reason で明示する
        caution=failure_caution(EXPERIMENT_ID),
    )


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["StageIncompleteError", "compute", "diff_lines_of", "dry_run", "main"]
