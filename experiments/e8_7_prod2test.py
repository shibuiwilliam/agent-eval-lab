"""E8-7 本番→テスト・パイプライン（原典 8.2）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version, load_tasks
from agenteval.drift import distribution as dist_mod
from agenteval.drift import draft as draft_mod
from agenteval.drift import prod2test as prod2test_mod
from agenteval.drift.prodstream import NEW_CATEGORY_TASKS, ProdStream, new_category_task
from agenteval.reports import plotting

DAYS = [1, 2, 3]
N_PER_DAY = 20
SAMPLE = 20

METHOD = """`ProdStream` で 3 日分のセッション（各 20 件）を生成し、既存タスクに対応するものは
そのタスクとして v01_baseline で実行して「本番トラフィック」の run 群を作った（ADR-006）。
層化サンプラーは失敗 run・高コスト run（種別 p95 超）・新カテゴリを層として各層から等数を採り、
対照は一様サンプリング。lift は「層化が採った重要セッション（失敗／高コスト／新カテゴリ）の割合 ÷
一様の同じ割合」。
採取した run からジャッジに `draft_task` 相当の起草をさせ（live が無いため決定的な起草器を使用）、
受入基準が既知の検査関数だけで書けているかを承認条件として承認率を出した（`--auto-approve` 相当。
結果ページに「自動承認」と明記する）。
登録前後で、スイートのカテゴリ分布と本番のカテゴリ分布の JS ダイバージェンスを比較した。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    stream = ProdStream(seed=seed)
    tasks = load_tasks()
    version = get_version("v01_baseline")

    prod_runs = []
    sessions = []
    for day in DAYS:
        for session in stream.day_sessions(day, N_PER_DAY):
            sessions.append(session)
            if session.task_id in tasks:
                task = get_task(session.task_id)
            else:
                prompt = dict(NEW_CATEGORY_TASKS)[session.task_id]
                task = new_category_task(session.task_id, prompt)
            run = run_task(
                task,
                version,
                RunOptions(
                    mode="sim",
                    seed=seed + day,
                    repeat=day,
                    keep_snapshots=False,
                    run_id_override=f"prod__{session.session_id}",
                ),
            )
            prod_runs.append(run)

    p95 = float(np.percentile([r.cost.total_tokens for r in prod_runs], 95))

    def importance(run: Any) -> bool:
        """重要セッション = 失敗・高コスト・新カテゴリ（合格基準の文言どおり）。"""
        return (not run.passed()) or run.cost.total_tokens > p95 or run.task_id.startswith("P-")

    stratified = prod2test_mod.stratified_sample(prod_runs, SAMPLE, p95, seed=seed)
    uniform = prod2test_mod.uniform_sample(prod_runs, SAMPLE, seed=seed)
    strat_rate = float(np.mean([importance(r) for r in stratified])) if stratified else 0.0
    uniform_rate = float(np.mean([importance(r) for r in uniform])) if uniform else 0.0
    lift = strat_rate / uniform_rate if uniform_rate else float("inf")

    draft_dir = Path("data/drafts_e8_7")
    drafts = []
    for index, run in enumerate(stratified):
        session = next(
            (s for s in sessions if s.session_id == run.run_id.replace("prod__", "")), None
        )
        prompt = session.prompt if session else get_task(run.task_id).prompt
        category = session.category if session else get_task(run.task_id).category
        draft = draft_mod.draft_from_run(run, prompt, category, index)
        draft_mod.save_draft(draft, draft_dir)
        drafts.append(draft)
    approved = [d for d in drafts if draft_mod.acceptance_is_checkable(d)]
    approval_rate = len(approved) / len(drafts) if drafts else 0.0

    suite_before = dist_mod.suite_distribution([t.category for t in tasks.values()])
    prod_dist = dist_mod.suite_distribution([s.category for s in sessions])
    js_before = dist_mod.js_divergence(suite_before, prod_dist)
    # 起草されたテストのカテゴリは、元になった本番セッションのカテゴリで数える
    # （新カテゴリを取り込めたかどうかを分布に反映させるため）
    approved_categories = []
    for draft in approved:
        session = next((s for s in sessions if s.prompt == draft["prompt"]), None)
        approved_categories.append(session.category if session else draft["category"])
    suite_after = dist_mod.suite_distribution(
        [t.category for t in tasks.values()] + approved_categories
    )
    js_after = dist_mod.js_divergence(suite_after, prod_dist)

    fig = plotting.bar_compare(
        "E8-7",
        "sampling",
        "重要セッション（失敗・高コスト）の採取率",
        ["層化サンプリング", "一様サンプリング（対照）"],
        [strat_rate, uniform_rate],
        "重要セッションの割合",
        "simulated (synthetic prod)",
    )

    metrics = {
        "stratified_lift": labeled(round(lift, 4)),
        "approval_rate": labeled(round(approval_rate, 4)),
        "js_after_lt_before": labeled(int(js_after < js_before)),
        "js_before": labeled(js_before),
        "js_after": labeled(js_after),
        "n_prod_runs": labeled(len(prod_runs)),
        "n_drafts": labeled(len(drafts)),
        "stratified_importance_rate": labeled(round(strat_rate, 4)),
        "uniform_importance_rate": labeled(round(uniform_rate, 4)),
    }
    notes = [
        "承認は自動承認（`--auto-approve` 相当）。承認条件は「受入基準が既知の検査関数だけで書けていること」。",
        (
            "新カテゴリ（expense）のセッションは `tasks/` に定義が無いので、その場限りの Task を"
            "組み立てて実行している（`prodstream.new_category_task`）。これが 8.2 の「本番にあって"
            "スイートに無いものを取り込む」経路にあたる。"
        ),
        f"起草されたテストのカテゴリ内訳: { {c: approved_categories.count(c) for c in set(approved_categories)} }",
        (
            "本番は生成器の代替である（ADR-006）。来歴は simulated (synthetic prod) とする。"
            "「実運用のセッションからテストを起草できた」とは主張しない。"
        ),
    ]
    notes.append(
        "判定は FAIL（実験設計の不備）。層化は重要セッションの採取率を 0.45 → 0.85 に上げたが、"
        "lift は 1.89 で基準の 3.0 に届かない。"
        "**この基準はこの本番ストリームでは原理的に達成できない**: 一様サンプリングでも 45% が"
        "重要セッション（失敗・高コスト・新カテゴリ）なので、lift の上限は 1 / 0.45 = 2.2 倍である。"
        "起草の承認率（1.0）と、登録後に JS が 0.100 → 0.018 に縮むことは成立した。"
        "修正案: 本番ストリームの失敗率を下げる（=重要セッションを希少にする）か、"
        "基準を lift ではなく採取率の絶対値で書く（ADR が要る）。"
    )
    return finalize("E8-7", metrics, METHOD, notes, figures=[("採取率", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
