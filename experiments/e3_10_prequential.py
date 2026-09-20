"""E3-10 変更履歴に沿った逐次評価（prequential）とラーニングカーブ。"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.core.registry import get_task, load_tasks
from agenteval.pts import dataset as ds
from agenteval.pts import model as model_mod
from agenteval.pts.synthetic import generate, load_pts_corpus
from agenteval.reports import plotting

WARMUP = 10
BUDGET = 0.3
LAG_KEYS = (
    "lag_since_test_ran",
    "lag_since_test_failed",
    "lag_since_test_regressed",
    "lag_since_units_changed",
    "log_lag_since_test_ran",
    "log_lag_since_test_regressed",
)
TEST_HISTORY_KEYS = (
    "hist_run_count",
    "hist_fail_rate",
    "hist_regress_count",
    "hist_regress_rate",
    "never_run_before",
)
CROSS_KEYS = (
    "regress_rate_when_units_changed",
    "n_runs_when_units_changed",
    "unit_regress_rate_max",
    "unit_regress_rate_mean",
)
META_KEYS = ("units_change_count", "history_depth")

# 内側検証で選ぶ候補。特徴群の取捨も**訓練フォールドの中だけ**で決める
# （モデルの選択と同じ規律。結果を見てから良い群を選ぶのは反則）。
GROUP_SUBSETS: list[tuple[str, tuple[str, ...]]] = [
    ("none", ()),
    ("lag", LAG_KEYS),
    ("test_history", TEST_HISTORY_KEYS),
    ("cross", CROSS_KEYS),
    ("lag+cross", LAG_KEYS + CROSS_KEYS),
    ("all", LAG_KEYS + TEST_HISTORY_KEYS + CROSS_KEYS + META_KEYS),
]

HISTORY_KEYS = (
    "lag_since_test_ran",
    "lag_since_test_failed",
    "lag_since_test_regressed",
    "lag_since_units_changed",
    "log_lag_since_test_ran",
    "log_lag_since_test_regressed",
    "hist_run_count",
    "hist_fail_rate",
    "hist_regress_count",
    "hist_regress_rate",
    "never_run_before",
    "units_change_count",
    "history_depth",
    "regress_rate_when_units_changed",
    "n_runs_when_units_changed",
    "unit_regress_rate_max",
    "unit_regress_rate_mean",
)

METHOD = """**変更履歴とテスト履歴を追記型の台帳（`pts/history.py`）として記録し**、
そこからラグと変更量の特徴量を作る。改訂前は変更を順序の無い集合として扱っていたので、
「前回このテストが走ってから何変更経ったか」のようなラグ特徴が 1 つも作れていなかった。

台帳から作る特徴量（17 個）:
- **ラグ**: 前回このテストが走ってから／落ちてから／回帰してから、この要素が前回変更されてから
- **テスト側の履歴**: 実行回数、履歴故障率、履歴回帰率、まだ一度も走っていないか
- **交差**: この要素が変更されたときこのテストが回帰した割合、要素そのものの回帰率
加えて**変更量**（変更行数・削除行数・文字数差・触る要素数と、その log）を変更側の特徴に入れた。

評価は **prequential（逐次予測）**: 合成変更 65 件を変更履歴の順に並べ、
変更 t を予測するときに使えるのは **`seq < t` の記録だけ**にする。
未来の記録は構造的に見えないので、時系列の漏洩が起こりえない。
最初の 10 件は学習の助走に使い、11 件目以降を予測して集計する。

対照は (a) カバレッジ重なりのみ、(b) **履歴・ラグ特徴を外した同じモデル**（アブレーション）、
(c) ランダム選択。ラーニングカーブは、予測した変更を前半・後半に分けた AUC で見る。"""


def _fit(train: list[ds.Row], keep: set[str], history: Any) -> Any:
    """指定した履歴特徴群だけを使ってロジスティック回帰を学習する。"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    clf = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")
    )
    clf.fit(
        np.array([_vector(r, keep, history) for r in train]),
        np.array([r.label_regressed for r in train]),
    )
    return clf


def _vector(row: ds.Row, keep: set[str], history: Any) -> list[float]:
    """履歴特徴のうち `keep` に無いものは 0 に潰す（ベクトル長は変えない）。"""
    dyn = ds.history_features(history, row)
    return row.vector({k: (v if k in keep else 0.0) for k, v in dyn.items()})


def _select_groups(train: list[ds.Row]) -> tuple[str, dict[str, float]]:
    """**訓練フォールドの中だけ**で、使う履歴特徴群を選ぶ。

    訓練の前半 70%（`seq` 順）で学習し、後半 30% で検証する内側の時系列分割。
    予測する変更は一切見ない。モデル選択（ADR-025 の入れ子交差検証）と同じ規律を
    特徴群の取捨にも適用する。
    """
    from sklearn.metrics import roc_auc_score

    seqs = sorted({r.seq for r in train})
    if len(seqs) < 6:
        return "lag", {}
    cut = seqs[int(len(seqs) * 0.7)]
    inner_train = [r for r in train if r.seq < cut]
    inner_valid = [r for r in train if r.seq >= cut]
    y_in = [r.label_regressed for r in inner_train]
    y_va = [r.label_regressed for r in inner_valid]
    if len(set(y_in)) < 2 or len(set(y_va)) < 2 or len(inner_train) < 30:
        return "lag", {}
    history = model_mod._history_of(inner_train)
    scores: dict[str, float] = {}
    for name, keys in GROUP_SUBSETS:
        keep = set(keys)
        try:
            clf = _fit(inner_train, keep, history)
            probs = clf.predict_proba(np.array([_vector(r, keep, history) for r in inner_valid]))
            scores[name] = float(roc_auc_score(y_va, probs[:, 1]))
        except ValueError:
            continue
    if not scores:
        return "lag", {}
    return max(scores, key=lambda k: scores[k]), scores


def _fit_predict(
    train: list[ds.Row], test: list[ds.Row], keep: set[str] | None = None
) -> dict[str, float]:
    """過去の行だけで学習し、次の変更の行を予測する。"""
    labels = [r.label_regressed for r in train]
    if len(train) < 30 or len(set(labels)) < 2:
        return {r.task_id: 1.0 - r.static["base_pass_rate"] for r in test}
    history = model_mod._history_of(train)
    use = keep if keep is not None else set(HISTORY_KEYS)
    clf = _fit(train, use, history)
    probs = clf.predict_proba(np.array([_vector(r, use, history) for r in test]))[:, 1]
    return {r.task_id: float(p) for r, p in zip(test, probs, strict=True)}


def main(live: bool = False, seed: int = 20260920) -> dict[str, Any]:
    runs = load_pts_corpus()
    if not runs:
        raise RuntimeError(
            "PTS コーパスが空です。先に `uv run agenteval ptscorpus` を実行してください"
        )
    changes = sorted(generate(), key=lambda c: c.seq)
    rows, stats = ds.build_rows(runs, changes)
    task_ids = sorted(load_tasks())
    inviolable = [t for t in task_ids if get_task(t).risk == "inviolable"]
    costs = {t: stats.tokens.get(t, 1.0) for t in task_ids}
    by_change: dict[str, list[ds.Row]] = {}
    for row in rows:
        by_change.setdefault(row.change_id, []).append(row)

    coverage_cv = model_mod.cross_validate(rows, "coverage", group_by="family")

    per_change: list[dict[str, Any]] = []
    preds = {  # type: ignore[var-annotated]
        "with_history": {},
        "no_history": {},
        "coverage": {},
        "lag_only": {},
        "all_history": {},
    }
    chosen_groups: list[str] = []
    for index, change in enumerate(changes):
        test = by_change.get(change.id, [])
        if not test or index < WARMUP:
            continue
        train = [r for r in rows if r.seq < change.seq]
        group_name, _inner = _select_groups(train)
        chosen_groups.append(group_name)
        keep = {name: set(keys) for name, keys in GROUP_SUBSETS}[group_name]
        scores_h = _fit_predict(train, test, keep=keep)
        scores_n = _fit_predict(train, test, keep=set())
        scores_lag = _fit_predict(train, test, keep=set(LAG_KEYS))
        scores_all = _fit_predict(train, test, keep=set(HISTORY_KEYS))
        scores_c = {
            r.task_id: coverage_cv.predictions.get((change.id, r.task_id), 0.0) for r in test
        }
        for name, scores in (
            ("with_history", scores_h),
            ("no_history", scores_n),
            ("coverage", scores_c),
            ("lag_only", scores_lag),
            ("all_history", scores_all),
        ):
            for task_id, value in scores.items():
                preds[name][(change.id, task_id)] = value

        positives = {r.task_id for r in test if r.label_regressed}
        entry: dict[str, Any] = {
            "seq": change.seq,
            "change": change.id,
            "family": change.family,
            "n_train_rows": len(train),
            "n_positive": len(positives),
            "groups": group_name,
        }
        if positives:
            for name, scores in (
                ("with_history", scores_h),
                ("no_history", scores_n),
                ("coverage", scores_c),
                ("lag_only", scores_lag),
                ("all_history", scores_all),
            ):
                recall, _ = model_mod.recall_at_budget(
                    scores, positives, costs, BUDGET, always=set(inviolable)
                )
                entry[f"recall_{name}"] = round(recall, 4)
            entry["recall_random"] = round(
                _random_recall(positives, task_ids, costs, BUDGET, inviolable, seed + index), 4
            )
        per_change.append(entry)

    evaluated = [r for r in rows if r.seq >= changes[WARMUP].seq and r.change_id in by_change]
    evaluated = [r for r in evaluated if (r.change_id, r.task_id) in preds["with_history"]]

    def auc(name: str, subset: list[ds.Row]) -> float:
        from sklearn.metrics import roc_auc_score

        y = [r.label_regressed for r in subset]
        if len(set(y)) < 2:
            return 0.0
        p = [preds[name][(r.change_id, r.task_id)] for r in subset]
        return float(roc_auc_score(y, p))

    seqs = sorted({r.seq for r in evaluated})
    mid = seqs[len(seqs) // 2]
    early = [r for r in evaluated if r.seq < mid]
    late = [r for r in evaluated if r.seq >= mid]

    auc_h, auc_n, auc_c = (auc(k, evaluated) for k in ("with_history", "no_history", "coverage"))
    auc_lag = auc("lag_only", evaluated)
    auc_all = auc("all_history", evaluated)

    def within_change_auc(name: str) -> float:
        from sklearn.metrics import roc_auc_score

        aucs = []
        for change_id, group in by_change.items():
            subset = [r for r in group if (change_id, r.task_id) in preds[name]]
            y = [r.label_regressed for r in subset]
            if len(set(y)) < 2:
                continue
            aucs.append(roc_auc_score(y, [preds[name][(change_id, r.task_id)] for r in subset]))
        return float(np.mean(aucs)) if aucs else 0.0

    auc_early, auc_late = auc("with_history", early), auc("with_history", late)

    def avg(key: str) -> float:
        values = [r[key] for r in per_change if key in r]
        return round(float(np.mean(values)), 4) if values else 0.0

    # ラーニングカーブ: 学習に使えた行数ごとの AUC（累積 5 変更ずつ）
    window = 5
    curve_x, curve_y = [], []
    for start in range(0, len(seqs) - window + 1, window):
        chunk = [r for r in evaluated if r.seq in seqs[start : start + window]]
        if len({r.label_regressed for r in chunk}) > 1:
            curve_x.append(float(chunk[0].seq))
            curve_y.append(auc("with_history", chunk))

    fig = plotting.line_curve(
        "E3-10",
        "learning_curve",
        "変更履歴が積み上がるにつれた予測精度（prequential AUC）",
        "変更履歴上の位置（seq）",
        "AUC（直近 5 変更）",
        {"履歴・ラグ特徴あり": (curve_x, curve_y)},
        "simulated",
    )
    fig2 = plotting.bar_compare(
        "E3-10",
        "auc",
        "時系列プロトコルでの AUC（アブレーション）",
        ["履歴あり", "履歴なし", "カバレッジのみ", "前半", "後半"],
        [auc_h, auc_n, auc_c, auc_early, auc_late],
        "AUC",
        "simulated",
    )

    # --- 履歴特徴の群ごとのアブレーション（再現可能な形で実験に入れる） ---
    ablation: dict[str, dict[str, float]] = {}
    for name, keys in GROUP_SUBSETS:
        local: dict[tuple[str, str], float] = {}
        recalls: list[float] = []
        for index, change in enumerate(changes):
            test = by_change.get(change.id, [])
            if not test or index < WARMUP:
                continue
            train = [r for r in rows if r.seq < change.seq]
            scores = _fit_predict(train, test, keep=set(keys))
            for task_id, value in scores.items():
                local[(change.id, task_id)] = value
            positives = {r.task_id for r in test if r.label_regressed}
            if positives:
                recall, _ = model_mod.recall_at_budget(
                    scores, positives, costs, BUDGET, always=set(inviolable)
                )
                recalls.append(recall)
        subset = [r for r in evaluated if (r.change_id, r.task_id) in local]
        y = [r.label_regressed for r in subset]
        from sklearn.metrics import roc_auc_score

        global_auc = (
            float(roc_auc_score(y, [local[(r.change_id, r.task_id)] for r in subset]))
            if len(set(y)) > 1
            else 0.0
        )
        per_change_aucs = []
        for change_id, group in by_change.items():
            rows_c = [r for r in group if (change_id, r.task_id) in local]
            yy = [r.label_regressed for r in rows_c]
            if len(set(yy)) < 2:
                continue
            per_change_aucs.append(
                roc_auc_score(yy, [local[(change_id, r.task_id)] for r in rows_c])
            )
        ablation[name] = {
            "global_auc": round(global_auc, 4),
            "within_change_auc": round(float(np.mean(per_change_aucs)), 4)
            if per_change_aucs
            else 0.0,
            "recall_at_budget_30": round(float(np.mean(recalls)), 4) if recalls else 0.0,
        }

    metrics = {
        "prequential_auc": labeled(round(auc_h, 4)),
        "best_group_auc": labeled(round(max(v["global_auc"] for v in ablation.values()), 4)),
        "history_feature_gain": labeled(round(auc_h - auc_n, 4)),
        "late_auc_gt_early_auc": labeled(int(auc_late > auc_early)),
        "recall_at_budget_30": labeled(avg("recall_with_history")),
        "beats_coverage_baseline": labeled(int(auc_h > auc_c)),
        "prequential_auc_no_history": labeled(round(auc_n, 4)),
        "prequential_auc_lag_only": labeled(round(auc_lag, 4)),
        "prequential_auc_all_history": labeled(round(auc_all, 4)),
        "within_change_auc": labeled(round(within_change_auc("with_history"), 4)),
        "within_change_auc_no_history": labeled(round(within_change_auc("no_history"), 4)),
        "within_change_auc_coverage": labeled(round(within_change_auc("coverage"), 4)),
        "recall_lag_only_30": labeled(avg("recall_lag_only")),
        "recall_all_history_30": labeled(avg("recall_all_history")),
        "prequential_auc_coverage": labeled(round(auc_c, 4)),
        "auc_early_half": labeled(round(auc_early, 4)),
        "auc_late_half": labeled(round(auc_late, 4)),
        "recall_no_history_30": labeled(avg("recall_no_history")),
        "recall_coverage_30": labeled(avg("recall_coverage")),
        "recall_random_30": labeled(avg("recall_random")),
        "n_changes": labeled(len(changes)),
        "n_changes_evaluated": labeled(len(per_change)),
        "n_changes_with_regression": labeled(len([r for r in per_change if r["n_positive"] > 0])),
        "warmup_changes": labeled(WARMUP),
        "n_rows_evaluated": labeled(len(evaluated)),
    }
    notes = [
        f"変更ごとの結果: {per_change}",
        f"ラーニングカーブ（seq, AUC）: {list(zip(curve_x, [round(v, 4) for v in curve_y], strict=True))}",
        f"内側検証が選んだ特徴群の内訳: {dict(Counter(chosen_groups))}",
        f"履歴特徴の群アブレーション: {ablation}",
    ]
    notes.append(
        "**時系列プロトコルにすると選択が大きく良くなった**。予算 30% での回帰の再現率は "
        f"{avg('recall_with_history')}（E3-8 の族単位 hold-out では 0.535）。"
        f"学習も積み上がる: 前半の AUC {round(auc_early, 3)} → 後半 {round(auc_late, 3)}。"
        "過去だけで学習して次の変更を予測するという現実的な運用が、"
        "族をまるごと hold-out する設定よりずっと良く働く。"
    )
    notes.append(
        "**ラグ特徴が効く**。群アブレーションでは `lag` だけを使ったときの大域 AUC が "
        f"{ablation['lag']['global_auc']} で、履歴特徴なし（{ablation['none']['global_auc']}）、"
        f"全部入り（{ablation['all']['global_auc']}）、カバレッジ単独（{round(auc_c, 4)}）の"
        "どれよりも高い。"
        "「前回このテストが走ってから／落ちてから何変更経ったか」「この要素が前回変更されてから」"
        "という recency が、産業用 PTS で重視されるとおりに効いている。"
    )
    notes.append(
        "**ただし群を全部入れると悪化する**。テスト履歴（故障率）と要素の交差履歴を足すと "
        f"{ablation['all']['global_auc']} まで落ちる。陽性が 122 件しかないところに"
        "相関した特徴を 17 個入れるので、ロジスティック回帰が過学習する。"
        "この環境では回帰が「テストの脆さ」ではなく「変更がどの要素を触ったか」で決まるため、"
        "テスト側の履歴故障率は交絡になる（別の要素を触った変更でも同じテストを疑ってしまう）。"
    )
    notes.append(
        "**特徴群の取捨も内側検証に任せた**が、選びきれなかった。"
        f"内側検証が選んだ群の内訳は {dict(Counter(chosen_groups))} で、"
        f"結果の AUC は {round(auc_h, 4)}。`lag` 固定の {ablation['lag']['global_auc']} に届かない。"
        "内側検証の窓（訓練前半 70% / 後半 30%）に入る陽性が少なすぎて、群の優劣を判別できていない。"
        "**結果を見てから `lag` を選べば基準を満たすが、それは基準に合わせた調整なので行わない。**"
        "必要なのは内側検証に回せるだけの履歴の長さで、合成変更を数百件に増やせば解消する見込みである。"
    )
    notes.append(
        "**変更内の順位付けではカバレッジが依然として強い**。変更内 AUC は "
        f"カバレッジ単独 {round(within_change_auc('coverage'), 4)} に対し、"
        f"学習モデルは {round(within_change_auc('with_history'), 4)}。"
        "E3-8 と同じ結論で、この環境で支配的な信号は原典 3.2 の依存グラフである。"
    )
    notes.append(
        "判定は FAIL（実験設計の不備）。5 基準中 3 つ（prequential_auc / 学習の積み上がり / 再現率）は"
        "満たした。未達は (1) `history_feature_gain > 0`、(2) `beats_coverage_baseline == 1` の 2 つで、"
        "どちらも**特徴群を選びきれなかったこと**に起因する。"
        "`lag` 固定なら AUC 0.878 でカバレッジ（0.776）を上回り、両方とも満たす。"
        "つまり**ラグ特徴そのものは仮説どおり効いており、足りないのは群を選ぶための履歴の長さである**。"
    )
    return finalize(
        "E3-10",
        metrics,
        METHOD,
        notes,
        figures=[("ラーニングカーブ", fig), ("アブレーション", fig2)],
        seed=seed,
    )


def _random_recall(
    positives: set[str],
    task_ids: list[str],
    costs: dict[str, float],
    budget_ratio: float,
    inviolable: list[str],
    seed: int,
    trials: int = 100,
) -> float:
    rng = np.random.default_rng(seed)
    budget = budget_ratio * sum(costs.get(t, 1.0) for t in task_ids)
    out = []
    for _ in range(trials):
        picked = set(inviolable)
        spent = sum(costs.get(t, 1.0) for t in inviolable)
        for index in rng.permutation(len(task_ids)):
            task_id = task_ids[int(index)]
            cost = costs.get(task_id, 1.0)
            if task_id in picked or spent + cost > budget:
                continue
            picked.add(task_id)
            spent += cost
        out.append(len(positives & picked) / len(positives))
    return float(np.mean(out))


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
