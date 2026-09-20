"""変更 × テスト → 回帰確率 の教師あり予測（原典 3.4 の `p̂_t` を学習で出す）。

原典 3.4 は `p̂_t` を「特徴量から推定する」と書いているだけで、推定の中身は指定していない。
改訂前の実装は Beta 事後（テストの履歴合格率）と小さなロジスティック回帰の平均で、
**変更 Δ にほとんど依存しない量**になっていた。ここでは産業用 PTS と同じく
「この変更でこのテストが落ちる確率」を直接学習する。

交差検証は**変更の族（family）単位の leave-one-group-out**。
同じ族（例: `max_steps` を 3/4/5/6 に下げる 4 件）は互いに似ているので、変更 1 件だけを
hold-out すると「同じ摂動の別の値」を見て当てているだけになる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

import numpy as np

from agenteval.pts.dataset import Row, history_features

ModelName = Literal["auto", "logistic", "gbdt", "gbdt_small", "prior", "coverage"]

# `auto` が内側交差検証で選ぶ候補。決め打ちで 1 つに決めると、後から
# 「結果を見て良いほうを選んだ」ことになる。選択は訓練フォールドの中だけで行う。
CANDIDATES: tuple[ModelName, ...] = ("logistic", "gbdt_small", "gbdt")


@dataclass
class CVResult:
    """交差検証の結果。"""

    model: str
    label: str
    predictions: dict[tuple[str, str], float] = field(default_factory=dict)
    roc_auc: float = 0.0
    average_precision: float = 0.0
    brier: float = 0.0
    base_rate: float = 0.0
    n_rows: int = 0
    n_positive: int = 0
    n_groups: int = 0
    feature_importance: dict[str, float] = field(default_factory=dict)
    within_change_auc: float = 0.0
    chosen_per_fold: dict[str, str] = field(default_factory=dict)
    inner_scores: dict[str, dict[str, float]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "label": self.label,
            "roc_auc": round(self.roc_auc, 4),
            "within_change_auc": round(self.within_change_auc, 4),
            "average_precision": round(self.average_precision, 4),
            "brier": round(self.brier, 4),
            "base_rate": round(self.base_rate, 4),
            "lift_over_base": round(self.average_precision / self.base_rate, 4)
            if self.base_rate
            else 0.0,
            "n_rows": self.n_rows,
            "n_positive": self.n_positive,
            "n_groups": self.n_groups,
            "chosen_per_fold": self.chosen_per_fold,
        }


def _make(model_name: ModelName) -> Any:
    if model_name == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0),
        )
    if model_name == "gbdt":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_iter=200, max_depth=4, learning_rate=0.08, min_samples_leaf=10, random_state=0
        )
    if model_name == "gbdt_small":
        # 陽性が 100 件程度しかないので、強めに正則化した版も候補に入れる
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_iter=60,
            max_depth=2,
            learning_rate=0.05,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=0,
        )
    raise ValueError(f"未知のモデル: {model_name}")


def _inner_select(
    train: list[Row], label: str, group_by: str, dyn: Any
) -> tuple[ModelName, dict[str, float]]:
    """訓練フォールドの中だけで候補モデルを比べ、AUC が最大のものを選ぶ。

    **外側の hold-out は一切見ない**。これをやらずにモデルを決め打ちすると、
    後から「結果を見て良いほうを選んだ」ことになる。
    """
    from sklearn.metrics import roc_auc_score

    inner_groups = sorted({getattr(r, group_by) for r in train})
    scores: dict[str, float] = {}
    for name in CANDIDATES:
        preds: list[float] = []
        truth: list[int] = []
        for group in inner_groups:
            fit_rows = [r for r in train if getattr(r, group_by) != group]
            val_rows = [r for r in train if getattr(r, group_by) == group]
            y_fit = np.array([_label_of(r, label) for r in fit_rows])
            if not val_rows or len(set(y_fit.tolist())) < 2:
                continue
            x_fit = np.array([r.vector(dyn(r, fit_rows)) for r in fit_rows])
            x_val = np.array([r.vector(dyn(r, fit_rows)) for r in val_rows])
            clf = _make(name)
            clf.fit(x_fit, y_fit)
            preds.extend(clf.predict_proba(x_val)[:, 1].tolist())
            truth.extend(_label_of(r, label) for r in val_rows)
        if truth and len(set(truth)) > 1:
            scores[name] = float(roc_auc_score(truth, preds))
    if not scores:
        return "logistic", scores
    best = max(scores, key=lambda k: scores[k])
    return cast("ModelName", best), scores


def _label_of(row: Row, label: str) -> int:
    return row.label_regressed if label == "regressed" else row.label_failed


def cross_validate(
    rows: list[Row],
    model_name: ModelName = "gbdt",
    label: str = "regressed",
    group_by: str = "family",
) -> CVResult:
    """族単位の leave-one-group-out 交差検証。

    履歴特徴（過去の変更のラベルを使うもの）は**訓練フォールドだけ**から計算する。
    """
    groups = sorted({getattr(r, group_by) for r in rows})
    ys = np.array([_label_of(r, label) for r in rows])
    result = CVResult(
        model=model_name,
        label=label,
        n_rows=len(rows),
        n_positive=int(ys.sum()),
        n_groups=len(groups),
        base_rate=float(ys.mean()) if len(ys) else 0.0,
    )
    if model_name in ("prior", "coverage"):
        for row in rows:
            key = (row.change_id, row.task_id)
            result.predictions[key] = (
                1.0 - row.static["base_pass_rate"]
                if model_name == "prior"
                else row.static["overlap_ratio"]
            )
        _score(result, rows, label)
        return result

    from agenteval.pts.change import ChangeKind  # noqa: F401  (型の意図を明示するため)

    importances: dict[str, list[float]] = {}
    for group in groups:
        train = [r for r in rows if getattr(r, group_by) != group]
        test = [r for r in rows if getattr(r, group_by) == group]
        if not train or not test:
            continue
        train_y = np.array([_label_of(r, label) for r in train])
        if len(set(train_y.tolist())) < 2:
            for row in test:
                result.predictions[(row.change_id, row.task_id)] = float(train_y.mean())
            continue

        def dyn(
            row: Row, pool: list[Row] | None = None, _train: list[Row] = train
        ) -> dict[str, float]:
            return history_features(_history_of(pool if pool is not None else _train), row)

        chosen: ModelName = model_name
        if model_name == "auto":
            chosen, inner = _inner_select(train, label, group_by, dyn)
            result.chosen_per_fold[str(group)] = chosen
            result.inner_scores[str(group)] = {k: round(v, 4) for k, v in inner.items()}

        names = train[0].names(dyn(train[0]))
        x_train = np.array([r.vector(dyn(r)) for r in train])
        x_test = np.array([r.vector(dyn(r)) for r in test])
        clf = _make(chosen)
        clf.fit(x_train, train_y)
        probs = clf.predict_proba(x_test)[:, 1]
        for row, p in zip(test, probs, strict=True):
            result.predictions[(row.change_id, row.task_id)] = float(p)
        for name, value in _importance(clf, names, x_train, train_y).items():
            importances.setdefault(name, []).append(value)

    result.feature_importance = {
        k: round(float(np.mean(v)), 4) for k, v in sorted(importances.items())
    }
    _score(result, rows, label)
    return result


def _history_of(rows: list[Row]) -> Any:
    """行の集合から変更履歴の台帳を組み立てる（`seq` 順）。"""
    from agenteval.pts.history import ChangeRecord, History, TestRunRecord

    history = History()
    seen: dict[str, Row] = {}
    for row in rows:
        seen.setdefault(row.change_id, row)
    for change_id, sample in sorted(seen.items(), key=lambda kv: kv[1].seq):
        kind = next(
            (
                k
                for k in ("prompt", "tool", "config", "model", "fixture", "code")
                if sample.static.get(f"change_kind_{k}", 0.0) == 1.0
            ),
            "prompt",
        )
        history.add_change(
            ChangeRecord(
                seq=sample.seq,
                change_id=change_id,
                kind=kind,
                family=sample.family,
                units=set(sample.units),
                churn={},
            )
        )
    for row in rows:
        history.add_run(
            TestRunRecord(
                seq=row.seq,
                change_id=row.change_id,
                task_id=row.task_id,
                passed=not row.label_failed,
                regressed=bool(row.label_regressed),
            )
        )
    return history


def _change_stub(row: Row) -> Any:
    """`history_features` は kind と components しか見ないので、行から復元する。"""
    from agenteval.core.schema import Change

    kind = next(
        (
            k
            for k in ("prompt", "tool", "config", "model", "fixture", "code")
            if row.static.get(f"change_kind_{k}", 0.0) == 1.0
        ),
        "prompt",
    )
    return Change(kind=kind, base="v01_baseline", target=row.change_id, components=set())


def _importance(clf: Any, names: list[str], x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """係数または permutation importance。"""
    try:
        from sklearn.linear_model import LogisticRegression

        step = clf[-1] if hasattr(clf, "__getitem__") else clf
        if isinstance(step, LogisticRegression):
            coef = np.abs(step.coef_[0])
            total = coef.sum() or 1.0
            return dict(zip(names, (coef / total).tolist(), strict=True))
    except (TypeError, AttributeError, IndexError):
        pass
    from sklearn.inspection import permutation_importance

    out = permutation_importance(clf, x, y, n_repeats=3, random_state=0, scoring="roc_auc")
    total = float(np.abs(out.importances_mean).sum()) or 1.0
    return dict(zip(names, (np.abs(out.importances_mean) / total).tolist(), strict=True))


def _score(result: CVResult, rows: list[Row], label: str) -> None:
    """大域の指標に加えて、**変更ごとの** AUC の平均も出す（ADR-027）。

    選択は変更ごとに行われるので、実際に効くのは変更内の順位付けの質である。
    """
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    by_change: dict[str, list[Row]] = {}
    for row in rows:
        by_change.setdefault(row.change_id, []).append(row)
    per_change_auc = []
    for change_id, group in by_change.items():
        truth = [_label_of(r, label) for r in group]
        if len(set(truth)) < 2:
            continue
        probs = [result.predictions.get((change_id, r.task_id), 0.0) for r in group]
        per_change_auc.append(roc_auc_score(truth, probs))
    result.within_change_auc = float(np.mean(per_change_auc)) if per_change_auc else 0.0

    y = np.array([_label_of(r, label) for r in rows])
    p = np.array([result.predictions.get((r.change_id, r.task_id), 0.0) for r in rows])
    if len(set(y.tolist())) < 2:
        return
    result.roc_auc = float(roc_auc_score(y, p))
    result.average_precision = float(average_precision_score(y, p))
    scaled = (p - p.min()) / (p.max() - p.min()) if p.max() > p.min() else p
    result.brier = float(brier_score_loss(y, scaled))


def recall_at_budget(
    scores: dict[str, float],
    positives: set[str],
    costs: dict[str, float],
    budget_ratio: float,
    always: set[str] | None = None,
) -> tuple[float, list[str]]:
    """予算比のもとでスコア降順に選んだときの、陽性の再現率（産業用 PTS の標準指標）。

    `always` は不可侵集合（選択ロジックの外で必ず実行される。原典 3.8）。
    """
    task_ids = sorted(scores)
    budget = budget_ratio * sum(costs.get(t, 1.0) for t in task_ids)
    selected = list(always or [])
    spent = sum(costs.get(t, 1.0) for t in selected)
    # 予算がトークンなので、詰める順は `score / cost`（予算付きナップサックの標準的な貪欲近似）。
    # 生のスコア順だと、同じスコアなら高いテストを先に取ってしまう。
    for task_id in sorted(
        task_ids, key=lambda t: scores[t] / max(1.0, costs.get(t, 1.0)), reverse=True
    ):
        if task_id in selected:
            continue
        cost = costs.get(task_id, 1.0)
        if spent + cost > budget:
            continue
        selected.append(task_id)
        spent += cost
    if not positives:
        return 1.0, selected
    caught = len(positives & set(selected))
    return caught / len(positives), selected
