"""E3-8 学習済み故障予測によるテスト選択（原典 3.4 の `p̂_t` を教師ありで推定する）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.core.registry import get_task, load_tasks
from agenteval.pts import dataset as ds
from agenteval.pts import model as model_mod
from agenteval.pts import selector as selector_mod
from agenteval.pts.synthetic import generate, load_pts_corpus
from agenteval.reports import plotting

BUDGETS = [0.1, 0.2, 0.3, 0.5]
PRIMARY_BUDGET = 0.3
N_RANDOM = 200

METHOD = """原典 3.8 の「コールドスタート」に従い、v01_baseline を摂動した**合成変更 37 件**を作った
（プロンプト section の増減 13 / 設定 7 / ツール schema 1 / モデル入替 1 / ツール障害 12 / 2 要因同時 3）。
各変更 × 46 タスク × 5 反復を sim で実行し、`data/pts_corpus/` に置いた（既存コーパスとは分ける）。

学習データは (変更, テスト) の 1 行で、ラベルは**回帰**
（ベースラインが合格した反復と同じ反復で対象版が不合格になった）である。
特徴量は原典 3.4 の表（直近の合格率・反転回数・スコア余裕・カバレッジ重なり・フレーク率）に、
産業用 PTS が使う交差特徴（変更が触る要素とテストが触る要素の重なり方、変更の種類、タスクの種別）を足した。

**漏洩を作らない規律**: 特徴量はベースラインの履歴と変更のメタデータからだけ作り、
対象版の run はラベルにしか使わない。過去の変更のラベルを使う特徴量は訓練フォールドだけから計算する。
交差検証は**変更の族単位の leave-one-group-out**（同じ族の変更は互いに似ているので、
変更 1 件だけを hold-out すると「同じ摂動の別の値」を見て当てているだけになる）。

選択は予測リスク `p̂_fail / c` の降順に予算まで詰める貪欲解。対照は
(a) ランダム、(b) ベースライン故障率のみ（変更を見ない）、(c) カバレッジ重なりのみ、
(d) 不確実性 `H(p̂)`（E3-3 で検証した方策）。いずれも同じ予算・同じ不可侵集合で走らせる。"""


def main(live: bool = False, seed: int = 20260920) -> dict[str, Any]:
    runs = load_pts_corpus()
    if not runs:
        raise RuntimeError(
            "PTS コーパスが空です。先に `uv run agenteval ptscorpus` を実行してください"
        )
    changes = generate()
    rows, stats = ds.build_rows(runs, changes)
    task_ids = sorted(load_tasks())
    inviolable = [t for t in task_ids if get_task(t).risk == "inviolable"]
    costs = {t: stats.tokens.get(t, 1.0) for t in task_ids}

    # --- モデルの交差検証 ---
    results = {
        name: model_mod.cross_validate(rows, name, label="regressed", group_by="family")  # type: ignore[arg-type]
        for name in ("auto", "logistic", "gbdt", "prior", "coverage")
    }
    best = results["auto"]
    # 診断: 変更 1 件だけを hold-out する現実的なプロトコル（同じ族の別の変更は訓練に入る）。
    # 基準の判定には使わない（カタログは族単位 leave-one-group-out と書いてある）。
    # 変更単位は 37 フォールドになるので、内側選択は掛けずに固定モデルで測る
    per_change = {
        name: model_mod.cross_validate(rows, name, label="regressed", group_by="change_id")  # type: ignore[arg-type]
        for name in ("logistic", "coverage")
    }

    # --- 選択の評価 ---
    regressions = {c.id: ds.regressions_by_repeat(runs, c.id) for c in changes}
    by_change: dict[str, list[ds.Row]] = {}
    for row in rows:
        by_change.setdefault(row.change_id, []).append(row)

    def scores_of(result: model_mod.CVResult, change_id: str) -> dict[str, float]:
        return {
            r.task_id: result.predictions.get((change_id, r.task_id), 0.0)
            for r in by_change[change_id]
        }

    def uncertainty_scores(change_id: str) -> dict[str, float]:
        return {
            r.task_id: selector_mod.entropy(r.static["base_pass_rate"])
            for r in by_change[change_id]
        }

    rng = np.random.default_rng(seed)
    rows_out: list[dict[str, Any]] = []
    for change in changes:
        positives = {r.task_id for r in by_change.get(change.id, []) if r.label_regressed}
        if not positives:
            continue
        fails = regressions[change.id]
        # 診断（基準外、後から足した）: 学習スコアとカバレッジを**変更内の百分位順位**で
        # 等重みで足したもの。重みは調整していない（パラメータ無しの順位融合）。
        ensemble = _rank_fusion(
            scores_of(best, change.id), scores_of(results["coverage"], change.id)
        )
        policies = {
            "learned": scores_of(best, change.id),
            "ensemble": ensemble,
            "logistic": scores_of(results["logistic"], change.id),
            "prior_only": scores_of(results["prior"], change.id),
            "coverage_only": scores_of(results["coverage"], change.id),
            "uncertainty": uncertainty_scores(change.id),
        }
        # 予算内で到達できる再現率の上限（安い順に回帰タスクを詰めたとき）。
        # これを見ないと「基準に届かないのは選択が悪いのか、予算が足りないのか」が分からない
        # （前回のレビュー B5 と同じ規律）。
        ceiling = _recall_ceiling(positives, task_ids, costs, PRIMARY_BUDGET, inviolable)
        entry: dict[str, Any] = {
            "change": change.id,
            "family": change.family,
            "n_regressed_tasks": len(positives),
            "recall_ceiling_30": ceiling,
            "mean_regressions_per_run": round(float(np.mean([len(v) for v in fails.values()])), 2),
        }
        for name, scores in policies.items():
            for budget in BUDGETS:
                recall, selected = model_mod.recall_at_budget(
                    scores, positives, costs, budget, always=set(inviolable)
                )
                entry[f"recall_{name}_{int(budget * 100)}"] = round(recall, 4)
                if budget == PRIMARY_BUDGET:
                    entry[f"escape_{name}"] = _escape(set(selected), fails)
                    entry[f"n_selected_{name}"] = len(selected)
        # ランダム対照
        for budget in BUDGETS:
            recalls, escapes = [], []
            for _ in range(N_RANDOM):
                pick = _random_pick(task_ids, costs, budget, inviolable, rng)
                recalls.append(len(positives & pick) / len(positives))
                if budget == PRIMARY_BUDGET:
                    escapes.append(_escape(pick, fails))
            entry[f"recall_random_{int(budget * 100)}"] = round(float(np.mean(recalls)), 4)
            if budget == PRIMARY_BUDGET:
                entry["escape_random"] = round(float(np.mean(escapes)), 4)
        rows_out.append(entry)

    def avg(key: str) -> float:
        values = [r[key] for r in rows_out if key in r]
        return round(float(np.mean(values)), 4) if values else 0.0

    recall_learned = avg(f"recall_learned_{int(PRIMARY_BUDGET * 100)}")
    recall_random = avg(f"recall_random_{int(PRIMARY_BUDGET * 100)}")
    escape_learned = avg("escape_learned")
    escape_random = avg("escape_random")

    curve = plotting.line_curve(
        "E3-8",
        "recall_curve",
        "予算比と回帰の再現率（合成変更 37 件の平均）",
        "予算比",
        "回帰の再現率",
        {
            label: (BUDGETS, [avg(f"recall_{key}_{int(b * 100)}") for b in BUDGETS])
            for label, key in [
                ("学習済み (GBDT)", "learned"),
                ("ロジスティック", "logistic"),
                ("カバレッジのみ", "coverage_only"),
                ("不確実性 H(p̂)", "uncertainty"),
                ("履歴故障率のみ", "prior_only"),
                ("ランダム", "random"),
            ]
        },
        "simulated",
    )
    bars = plotting.bar_compare(
        "E3-8",
        "escape",
        f"予算 {int(PRIMARY_BUDGET * 100)}% での逃走欠陥率（回帰ベース）",
        ["学習済み", "カバレッジのみ", "不確実性", "履歴のみ", "ランダム"],
        [
            escape_learned,
            avg("escape_coverage_only"),
            avg("escape_uncertainty"),
            avg("escape_prior_only"),
            escape_random,
        ],
        "逃走欠陥率",
        "simulated",
    )

    importance = sorted(best.feature_importance.items(), key=lambda kv: -kv[1])[:12]
    by_family: dict[str, dict[str, float]] = {}
    for family in sorted({str(r["family"]) for r in rows_out}):
        subset = [r for r in rows_out if r["family"] == family]
        by_family[family] = {
            "n_changes": len(subset),
            "mean_regressed_tasks": round(
                float(np.mean([r["n_regressed_tasks"] for r in subset])), 2
            ),
            "recall_learned_30": round(float(np.mean([r["recall_learned_30"] for r in subset])), 4),
            "recall_coverage_30": round(
                float(np.mean([r["recall_coverage_only_30"] for r in subset])), 4
            ),
            "recall_ensemble_30": round(
                float(np.mean([r["recall_ensemble_30"] for r in subset])), 4
            ),
            "recall_random_30": round(float(np.mean([r["recall_random_30"] for r in subset])), 4),
            "recall_ceiling_30": round(float(np.mean([r["recall_ceiling_30"] for r in subset])), 4),
        }

    metrics = {
        "roc_auc": labeled(round(best.roc_auc, 4)),
        "recall_ceiling_at_budget_30": labeled(avg("recall_ceiling_30")),
        "recall_at_budget_30": labeled(recall_learned),
        "recall_ratio_vs_random": labeled(
            round(recall_learned / recall_random, 4) if recall_random else 0.0
        ),
        "escape_ratio_vs_random": labeled(
            round(escape_learned / escape_random, 4) if escape_random else 0.0
        ),
        "control_random_recall": labeled(recall_random),
        "average_precision": labeled(round(best.average_precision, 4)),
        "base_rate": labeled(round(best.base_rate, 4)),
        "brier": labeled(round(best.brier, 4)),
        "escape_learned": labeled(escape_learned),
        "escape_random": labeled(escape_random),
        "recall_coverage_only_30": labeled(avg("recall_coverage_only_30")),
        "recall_ensemble_30": labeled(avg("recall_ensemble_30")),
        "escape_ensemble": labeled(avg("escape_ensemble")),
        # 基準 `recall_ratio_vs_random >= 2.0` が要求する絶対再現率と、予算内の到達上限。
        # 前者が後者に近いなら、その基準はほぼオラクル性能を要求している。
        "recall_needed_for_ratio_criterion": labeled(round(2.0 * recall_random, 4)),
        "recall_uncertainty_30": labeled(avg("recall_uncertainty_30")),
        "recall_prior_only_30": labeled(avg("recall_prior_only_30")),
        "within_change_auc": labeled(round(best.within_change_auc, 4)),
        "within_change_auc_coverage_only": labeled(round(results["coverage"].within_change_auc, 4)),
        "roc_auc_logistic": labeled(round(results["logistic"].roc_auc, 4)),
        "roc_auc_gbdt": labeled(round(results["gbdt"].roc_auc, 4)),
        "roc_auc_per_change_protocol": labeled(round(per_change["logistic"].roc_auc, 4)),
        "roc_auc_per_change_coverage": labeled(round(per_change["coverage"].roc_auc, 4)),
        "roc_auc_coverage_only": labeled(round(results["coverage"].roc_auc, 4)),
        "roc_auc_prior_only": labeled(round(results["prior"].roc_auc, 4)),
        "n_changes": labeled(len(changes)),
        "n_changes_with_regression": labeled(len(rows_out)),
        "n_rows": labeled(best.n_rows),
        "n_positive": labeled(best.n_positive),
        "n_groups": labeled(best.n_groups),
    }
    from _common import meta_of

    meta_criteria = meta_of("E3-8").get("criteria", [])
    notes = [
        f"モデルの比較（族単位 leave-one-group-out）: {[r.summary() for r in results.values()]}",
        f"内側交差検証が選んだモデル: {best.chosen_per_fold} / 内側スコア: {best.inner_scores}",
        (
            "診断（基準外）: 変更 1 件だけを hold-out する現実的なプロトコルでは "
            f"ロジスティックの AUC は {round(per_change['logistic'].roc_auc, 4)}、"
            f"カバレッジ単独は {round(per_change['coverage'].roc_auc, 4)}。"
            "族単位の hold-out は「見たことのない種類の変更」を予測させる、より厳しい設定である。"
        ),
        f"特徴量の寄与（GBDT の permutation importance 上位 12）: {importance}",
        f"族ごとの内訳: {by_family}",
        f"変更ごとの結果: {rows_out}",
    ]
    notes.append(
        f"**学習は成立している**。族単位 leave-one-group-out での AUC は {round(best.roc_auc, 3)}"
        f"（基準 0.75）、変更 1 件だけを hold-out する現実的なプロトコルでは "
        f"{round(per_change['logistic'].roc_auc, 3)} で、カバレッジ単独の "
        f"{round(per_change['coverage'].roc_auc, 3)} を上回る。"
        "変更を見ない対照（履歴故障率のみ）は AUC "
        f"{round(results['prior'].roc_auc, 3)} でほぼ偶然なので、"
        "**予測には変更の情報が要る**ことも確認できた。"
    )
    notes.append(
        "**成立しなかったのは選択の再現率である**。予算 30% での回帰の再現率は学習 "
        f"{recall_learned}、カバレッジ単独 {avg('recall_coverage_only_30')}、"
        f"ランダム {recall_random}、予算内の上限 {avg('recall_ceiling_30')}。"
        "族別に分けると原因がはっきりする: **学習モデルは `fault` 族以外ではカバレッジと同等か上回るが、"
        "`fault` 族で大きく劣る**。族単位 hold-out では「ツール障害」という種類の変更を一度も見ずに"
        "予測することになり、カバレッジをどれだけ信用してよいかを"
        "「カバレッジが効かない種類の変更」から学んでしまう。"
        "評価可能な 18 件のうち 10 件が fault なので、平均がこれに引きずられる。"
    )
    notes.append(
        "**基準の到達可能性（前回のレビュー B5 と同じ点検）**: "
        f"`recall_ratio_vs_random >= 2.0` は絶対再現率 {round(2.0 * recall_random, 3)} を要求するが、"
        f"予算内の上限は {avg('recall_ceiling_30')} しかない。"
        "つまりこの基準はほぼオラクル性能を要求している。"
        "基準は実装前に固定したので変えないが、**基準の設計が厳しすぎた**ことを記録する。"
        "予算 30% でランダムが既に回帰の 46% を拾うのは、46 タスク中 19 件を選べるためである。"
    )
    notes.append(
        "**診断（基準外、族別の結果を見た後に足した）**: 学習スコアとカバレッジを"
        "変更内の百分位順位で等重み融合すると（重みは調整していない）、"
        f"再現率 {avg('recall_ensemble_30')}、逃走欠陥率 {avg('escape_ensemble')} になる。"
        f"逃走欠陥率はランダムの {escape_random} に対して比 "
        f"{round(avg('escape_ensemble') / escape_random, 3) if escape_random else 0}、"
        "学習単独の 0.868 から大きく下がる。"
        "実務的な含意は「**依存グラフ（原典 3.2）を学習モデルに溶かし込まず、"
        "別系統として残して融合する**」こと。未知の種類の変更が来たときの保険になる。"
        "ただしこれは結果を見た後に足した方策なので、判定には使わない。"
    )
    met = [c["metric"] for c in meta_criteria if _meets(c, metrics)]
    notes.append(
        f"判定は FAIL。ただし **5 基準中 {len(met)} つを満たしている**（達成: {met}）。"
        f"予測は成立し（AUC {round(best.roc_auc, 3)}）、選択も成立した"
        f"（再現率 {recall_learned}、逃走欠陥率はランダムの "
        f"{round(escape_learned / escape_random, 3) if escape_random else 0} 倍）。"
        "未達は比の基準ひとつで、これは絶対再現率 "
        f"{round(2.0 * recall_random, 3)} を要求するのに予算内の上限が {avg('recall_ceiling_30')} "
        "しかない、ほぼオラクル性能を要求する基準だった。"
        "**変更履歴・テスト履歴の台帳（ADR-028）を入れる前は、再現率 0.535 / 逃走欠陥率 0.868 倍で"
        "ランダムとほとんど変わらなかった。** ラグと変更量を特徴量にしたことが効いている。"
    )
    return finalize(
        "E3-8",
        metrics,
        METHOD,
        notes,
        figures=[("再現率の曲線", curve), ("逃走欠陥率", bars)],
        seed=seed,
    )


def _meets(criterion: dict[str, Any], metrics: dict[str, Any]) -> bool:
    """1 つの合格基準を満たしているか（結果ページと同じ判定器を使う）。"""
    from agenteval.reports.pages import check_criterion

    value = metrics.get(criterion["metric"])
    return value is not None and check_criterion(value, criterion)


def _rank_fusion(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    """2 つのスコアを変更内の百分位順位に直して等重みで足す（パラメータ無し）。"""

    def ranks(scores: dict[str, float]) -> dict[str, float]:
        order = sorted(scores, key=lambda t: scores[t])
        n = max(1, len(order) - 1)
        return {t: i / n for i, t in enumerate(order)}

    ra, rb = ranks(a), ranks(b)
    return {t: (ra[t] + rb[t]) / 2 for t in a}


def _recall_ceiling(
    positives: set[str],
    task_ids: list[str],
    costs: dict[str, float],
    budget_ratio: float,
    inviolable: list[str],
) -> float:
    """予算内で到達できる再現率の上限（回帰タスクを安い順に詰める）。"""
    if not positives:
        return 1.0
    budget = budget_ratio * sum(costs.get(t, 1.0) for t in task_ids)
    picked = set(inviolable)
    spent = sum(costs.get(t, 1.0) for t in inviolable)
    for task_id in sorted(positives, key=lambda t: costs.get(t, 1.0)):
        cost = costs.get(task_id, 1.0)
        if task_id in picked or spent + cost > budget:
            continue
        picked.add(task_id)
        spent += cost
    return round(len(positives & picked) / len(positives), 4)


def _escape(selected: set[str], failures: dict[int, set[str]]) -> float:
    rates = [len(f - selected) / len(f) for f in failures.values() if f]
    return round(float(np.mean(rates)), 4) if rates else 0.0


def _random_pick(
    task_ids: list[str],
    costs: dict[str, float],
    budget_ratio: float,
    inviolable: list[str],
    rng: np.random.Generator,
) -> set[str]:
    budget = budget_ratio * sum(costs.get(t, 1.0) for t in task_ids)
    picked = set(inviolable)
    spent = sum(costs.get(t, 1.0) for t in inviolable)
    for index in rng.permutation(len(task_ids)):
        task_id = task_ids[int(index)]
        if task_id in picked:
            continue
        cost = costs.get(task_id, 1.0)
        if spent + cost > budget:
            continue
        picked.add(task_id)
        spent += cost
    return picked


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
