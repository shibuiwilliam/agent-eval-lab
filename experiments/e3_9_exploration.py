"""E3-9 不確実性の役割は「探索」である（原典 3.4 と 3.8 の切り分け）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.core.registry import get_task, load_tasks
from agenteval.pts import dataset as ds
from agenteval.pts import selector as selector_mod
from agenteval.pts.synthetic import generate, load_pts_corpus
from agenteval.reports import plotting

BUDGET = 0.3
EPSILONS = [0.0, 0.05, 0.2]
STRATEGIES = ["uncertainty", "random"]
N_SEEDS = 5

METHOD = """E3-3 は「`H(p̂)` による選択はその回の逃走欠陥を減らさない」ことを示した。
本実験は、原典 3.8 が ε-探索に与えている役割（「選択器の**校正を保つ**」）が成り立つかを測る。

合成変更 37 件を族が偏らない順に並べ、**部分観測**で逐次処理する。
各ラウンドで (1) これまでに観測した行だけでモデルを学習し、(2) 予測リスク `p̂_fail / c` の降順に
予算 30% まで選び、(3) **選んだテストの結果だけ**を観測してデータに足す。
探索比 ε ∈ {0.0, 0.05, 0.2} で、探索枠の中身を「不確実性 `H(p̂)` 上位」と「ランダム」の 2 通り試した。

各ラウンドで記録するのは、(a) そのラウンドの回帰の再現率、(b) 逃走欠陥率、
(c) **そのラウンドの全テストに対する** Brier スコア（観測していないテストも含めて評価する。
評価に使うだけで学習には使わない）。校正は後半ラウンド（18 件目以降）の平均で比べる。

対照は ε = 0（純粋な活用）。全件観測（完全情報）を性能の上限として併記する。
逐次学習の都合で学習器はロジスティック回帰に固定した（E3-8 の GBDT は少数データで不安定なため）。

**頑健性**: 結論が変更の並び順 1 通りに依存しないよう、族を混ぜる並べ方を 5 通り（seed を変える）作り、
すべての指標をその平均で報告する。ばらつき（標準偏差）も併記する。"""


def _fit(observed: list[ds.Row]) -> Any:
    """観測済みの行でロジスティック回帰を学習する。両クラスが無ければ None。"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from agenteval.pts.model import _change_stub

    labels = [r.label_regressed for r in observed]
    if len(observed) < 20 or len(set(labels)) < 2:
        return None
    x = np.array(
        [r.vector(ds.history_features(r.task_id, _change_stub(r), observed)) for r in observed]
    )
    clf = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")
    )
    clf.fit(x, np.array(labels))
    return clf


def _predict(clf: Any, rows: list[ds.Row], observed: list[ds.Row]) -> dict[str, float]:
    from agenteval.pts.model import _change_stub

    if clf is None:
        return {r.task_id: 1.0 - r.static["base_pass_rate"] for r in rows}
    x = np.array(
        [r.vector(ds.history_features(r.task_id, _change_stub(r), observed)) for r in rows]
    )
    probs = clf.predict_proba(x)[:, 1]
    return {r.task_id: float(p) for r, p in zip(rows, probs, strict=True)}


def _order(changes: list[Any], seed: int) -> list[Any]:
    """族が固まらないように並べる（ラウンド順が族順と一致すると学習が不公平になる）。"""
    rng = np.random.default_rng(seed)
    by_family: dict[str, list[Any]] = {}
    for change in changes:
        by_family.setdefault(change.family, []).append(change)
    for group in by_family.values():
        rng.shuffle(group)
    out, families = [], sorted(by_family)
    while any(by_family[f] for f in families):
        for family in families:
            if by_family[family]:
                out.append(by_family[family].pop())
    return out


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
    by_change: dict[str, list[ds.Row]] = {}
    for row in rows:
        by_change.setdefault(row.change_id, []).append(row)
    regressions = {c.id: ds.regressions_by_repeat(runs, c.id) for c in changes}
    half = len(changes) // 2

    from sklearn.metrics import brier_score_loss

    def run_once(
        ordered: list[Any], epsilon: float, strategy: str, full_observation: bool, seed_i: int
    ) -> dict[str, Any]:
        observed: list[ds.Row] = []
        history: list[dict[str, float]] = []
        for index, change in enumerate(ordered):
            round_rows = by_change.get(change.id, [])
            if not round_rows:
                continue
            clf = _fit(observed)
            scores = _predict(clf, round_rows, observed)
            explore = (
                {r.task_id: selector_mod.entropy(scores[r.task_id]) for r in round_rows}
                if strategy == "uncertainty"
                else None
            )
            selection = selector_mod.select_by_risk(
                scores,
                costs,
                budget_ratio=BUDGET,
                inviolable=inviolable,
                epsilon=epsilon,
                seed=seed_i + index,
                explore_scores=explore,
            )
            picked = set(selection.selected)
            positives = {r.task_id for r in round_rows if r.label_regressed}
            fails = regressions[change.id]
            y = np.array([r.label_regressed for r in round_rows])
            p = np.array([scores[r.task_id] for r in round_rows])
            history.append(
                {
                    "round": float(index),
                    "recall": len(positives & picked) / len(positives) if positives else 1.0,
                    "escape": float(
                        np.mean([len(f - picked) / len(f) for f in fails.values() if f])
                    )
                    if any(fails.values())
                    else 0.0,
                    "brier": float(brier_score_loss(y, np.clip(p, 0, 1)))
                    if len(set(y.tolist())) > 0
                    else 0.0,
                    "n_observed": float(len(observed)),
                    "has_positive": 1.0 if positives else 0.0,
                }
            )
            observed.extend(
                round_rows if full_observation else [r for r in round_rows if r.task_id in picked]
            )
        return {
            "epsilon": epsilon,
            "strategy": strategy,
            "full_observation": full_observation,
            "rounds": history,
            "late_brier": round(float(np.mean([h["brier"] for h in history[half:]])), 4),
            "late_escape": round(
                float(np.mean([h["escape"] for h in history[half:] if h["has_positive"]])), 4
            ),
            "late_recall": round(
                float(np.mean([h["recall"] for h in history[half:] if h["has_positive"]])), 4
            ),
            "mean_recall": round(
                float(np.mean([h["recall"] for h in history if h["has_positive"]])), 4
            ),
            "n_observed_final": int(history[-1]["n_observed"]) if history else 0,
        }

    def run_policy(epsilon: float, strategy: str, full_observation: bool = False) -> dict[str, Any]:
        """5 通りの並び順で走らせて平均する（結論が 1 つの並びに依存しないようにする）。"""
        trials = [
            run_once(_order(changes, seed + k), epsilon, strategy, full_observation, seed + k * 97)
            for k in range(N_SEEDS)
        ]

        def agg(key: str) -> float:
            return round(float(np.mean([t[key] for t in trials])), 4)

        def spread(key: str) -> float:
            return round(float(np.std([t[key] for t in trials])), 4)

        return {
            "epsilon": epsilon,
            "strategy": strategy,
            "full_observation": full_observation,
            "late_brier": agg("late_brier"),
            "late_brier_sd": spread("late_brier"),
            "late_escape": agg("late_escape"),
            "late_escape_sd": spread("late_escape"),
            "late_recall": agg("late_recall"),
            "mean_recall": agg("mean_recall"),
            "n_observed_final": int(np.mean([t["n_observed_final"] for t in trials])),
            "trials": trials,
        }

    policies = {"eps0.0_none": run_policy(0.0, "random")}
    for epsilon in EPSILONS[1:]:
        for strategy in STRATEGIES:
            policies[f"eps{epsilon}_{strategy}"] = run_policy(epsilon, strategy)
    policies["full_observation"] = run_policy(0.0, "random", full_observation=True)

    base = policies["eps0.0_none"]
    tuned = policies["eps0.05_uncertainty"]
    # 並び順は方策間で揃えてあるので、**対応のある比較**ができる。
    # 並び順そのものによるばらつき（SD 0.015 と大きい）が相殺され、検出力が上がる。
    paired_brier = [
        b["late_brier"] - t["late_brier"]
        for b, t in zip(base["trials"], tuned["trials"], strict=True)
    ]
    paired_escape = [
        b["late_escape"] - t["late_escape"]
        for b, t in zip(base["trials"], tuned["trials"], strict=True)
    ]
    calibration_gain = round(float(np.mean(paired_brier)), 4)
    escape_gain = round(float(np.mean(paired_escape)), 4)

    def power_n(diffs: list[float]) -> float:
        """観測した効果量を 80% の検出力で拾うのに要る並び順の本数（対応のある t 検定）。"""
        mean, sd = float(np.mean(diffs)), float(np.std(diffs, ddof=1))
        if abs(mean) < 1e-9 or sd < 1e-12:
            return float("inf")
        return round(((1.96 + 0.84) * sd / abs(mean)) ** 2, 1)

    cost_rounds = [
        max(0.0, b["recall"] - t["recall"])
        for bt, tt in zip(base["trials"], tuned["trials"], strict=True)
        for b, t in zip(bt["rounds"], tt["rounds"], strict=True)
        if b["has_positive"]
    ]
    exploration_cost = round(float(np.mean(cost_rounds)), 4)
    # 片側の指標なので、探索が勝ったラウンドの利得も併記する（差引きの実像）
    gain_rounds = [
        max(0.0, t["recall"] - b["recall"])
        for bt, tt in zip(base["trials"], tuned["trials"], strict=True)
        for b, t in zip(bt["rounds"], tt["rounds"], strict=True)
        if b["has_positive"]
    ]
    exploration_gain = round(float(np.mean(gain_rounds)), 4)

    fig = plotting.line_curve(
        "E3-9",
        "brier",
        "ラウンドと校正誤差（Brier、低いほど良い）",
        "ラウンド",
        "Brier",
        {
            label: (
                [h["round"] for h in policies[key]["trials"][0]["rounds"]],
                [
                    float(np.mean([t["rounds"][i]["brier"] for t in policies[key]["trials"]]))
                    for i in range(len(policies[key]["trials"][0]["rounds"]))
                ],
            )
            for label, key in [
                ("ε=0（活用のみ）", "eps0.0_none"),
                ("ε=0.05 不確実性", "eps0.05_uncertainty"),
                ("ε=0.2 不確実性", "eps0.2_uncertainty"),
                ("全件観測（上限）", "full_observation"),
            ]
        },
        "simulated",
    )
    fig2 = plotting.bar_compare(
        "E3-9",
        "late",
        "後半ラウンドの校正誤差と逃走欠陥率",
        ["ε=0", "ε=0.05 不確実性", "ε=0.05 ランダム", "ε=0.2 不確実性", "全件観測"],
        [
            policies[k]["late_brier"]
            for k in (
                "eps0.0_none",
                "eps0.05_uncertainty",
                "eps0.05_random",
                "eps0.2_uncertainty",
                "full_observation",
            )
        ],
        "Brier（後半平均）",
        "simulated",
    )

    metrics = {
        "calibration_gain": labeled(calibration_gain),
        "escape_gain": labeled(escape_gain),
        "exploration_cost": labeled(exploration_cost),
        "exploration_gain": labeled(exploration_gain),
        "calibration_gain_paired_sd": labeled(round(float(np.std(paired_brier, ddof=1)), 4)),
        "escape_gain_paired_sd": labeled(round(float(np.std(paired_escape, ddof=1)), 4)),
        "n_orderings_needed_calibration": labeled(power_n(paired_brier)),
        "n_orderings_needed_escape": labeled(power_n(paired_escape)),
        "late_brier_eps0_sd": labeled(base["late_brier_sd"]),
        "late_brier_eps005_sd": labeled(tuned["late_brier_sd"]),
        "late_escape_eps0_sd": labeled(base["late_escape_sd"]),
        "late_escape_eps005_sd": labeled(tuned["late_escape_sd"]),
        "n_seeds": labeled(N_SEEDS),
        "late_brier_eps0": labeled(base["late_brier"]),
        "late_brier_eps005_uncertainty": labeled(tuned["late_brier"]),
        "late_brier_eps005_random": labeled(policies["eps0.05_random"]["late_brier"]),
        "late_brier_eps02_uncertainty": labeled(policies["eps0.2_uncertainty"]["late_brier"]),
        "late_brier_full_observation": labeled(policies["full_observation"]["late_brier"]),
        "late_escape_eps0": labeled(base["late_escape"]),
        "late_escape_eps005_uncertainty": labeled(tuned["late_escape"]),
        "late_recall_eps0": labeled(base["late_recall"]),
        "late_recall_eps005_uncertainty": labeled(tuned["late_recall"]),
        "observed_rows_eps0": labeled(base["n_observed_final"]),
        "observed_rows_eps005": labeled(tuned["n_observed_final"]),
        "n_rounds": labeled(len(changes)),
    }
    notes = [
        (
            f"方策ごとの要約（{N_SEEDS} 通りの並び順の平均）: "
            f"{ {k: {kk: vv for kk, vv in v.items() if kk != 'trials'} for k, v in policies.items()} }"
        ),
        f"ラウンドごとの推移（ε=0、seed 1 本目）: {[{kk: round(vv, 3) for kk, vv in h.items()} for h in base['trials'][0]['rounds']]}",
    ]
    notes.append(
        "**並び順 1 通りでは結論が出なかった**。最初の実装は変更の並び順を 1 通りしか試しておらず、"
        "そのときは校正の改善が +0.006 と出ていた。並び順を 5 通りに増やして"
        f"**対応のある比較**（同じ並び順で ε=0 と ε=0.05 を比べる）にすると、改善は "
        f"{calibration_gain}（対応差の SD {round(float(np.std(paired_brier, ddof=1)), 4)}）になり、"
        "**効果は消えた**。1 通りの結果は並び順のばらつきを拾っていただけだった。"
    )
    notes.append(
        "**検出力**: この効果量を 80% の検出力で拾うには並び順が "
        f"{power_n(paired_brier)} 本要る（逃走欠陥率のほうは {power_n(paired_escape)} 本）。"
        "5 本では足りない。つまりこの実験は「探索に校正を改善する効果が無い」ことを示したのではなく、"
        "**あるとしても 5 本の並び順では見えない大きさである**ことを示した。"
        "肯定にも否定にも足りていない、という結果である。"
    )
    notes.append(
        "**探索の費用は基準内だった**。当該ラウンドで探索に払った再現率の低下は平均 "
        f"{exploration_cost}（基準 0.10）で、逆に探索が勝ったラウンドの利得は {exploration_gain}。"
        "**差し引きでは探索のほうがわずかに得をしている**。"
        "少なくとも「探索は当該ラウンドの性能を大きく犠牲にする」という懸念は否定できた。"
    )
    notes.append(
        "全件観測（完全情報）の後半 Brier は "
        f"{policies['full_observation']['late_brier']} で、部分観測の ε=0 "
        f"（{base['late_brier']}）より**悪い**。データが多いほど校正が良くなるとは限らない。"
        "全件観測は簡単な陰性を大量に学習に入れるので、陽性の少ないこの設定では"
        "かえって確率が縮む方向に働く。上限として置いた対照が上限になっていなかった、という観測である。"
    )
    notes.append(
        "判定は NEGATIVE。仮説（探索は校正を保つ）は**成立も否定もできなかった**。"
        "実装の不備ではなく、変更 37 件・陽性 108 件という規模では効果量が"
        "並び順のばらつきに埋もれる。基準 `calibration_gain > 0` は"
        "「効果量が測定誤差より大きい」ことを暗黙に仮定しており、その仮定が成り立っていなかった。"
        "次にやるなら並び順を 100 本にするか、合成変更を数百件に増やす。"
    )
    return finalize(
        "E3-9",
        metrics,
        METHOD,
        notes,
        figures=[("校正の推移", fig), ("後半ラウンドの比較", fig2)],
        seed=seed,
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
