"""本番→テストのサンプリング・起草・登録（原典 8.2）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agenteval.core.schema import Run


@dataclass
class Stratum:
    """層 1 つ。"""

    name: str
    runs: list[Run]


def stratify(runs: list[Run], p95_tokens: float) -> list[Stratum]:
    """層は「失敗 run・高コスト run・新カテゴリ」の 3 つ（.claude/rules/drift.md）。

    どの層にも入らない run は層にしない。層を使い切ったときの穴埋めにだけ使う。
    """
    failed = [r for r in runs if not r.passed()]
    costly = [r for r in runs if r.cost.total_tokens > p95_tokens]
    new_category = [r for r in runs if r.task_id.startswith("P-")]
    return [
        Stratum("failed", failed),
        Stratum("costly", costly),
        Stratum("new_category", new_category),
    ]


def stratified_sample(runs: list[Run], n: int, p95_tokens: float, seed: int = 0) -> list[Run]:
    """層化サンプリング。各層から等数を取り、足りない分は残りから埋める。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    strata = [s for s in stratify(runs, p95_tokens) if s.runs]
    if not strata:
        return []
    per = max(1, n // len(strata))
    picked: list[Run] = []
    for stratum in strata:
        order = rng.permutation(len(stratum.runs))
        picked.extend(stratum.runs[i] for i in order[:per])
    if len(picked) < n:
        remaining = [r for r in runs if r not in picked]
        order = rng.permutation(len(remaining))
        picked.extend(remaining[i] for i in order[: n - len(picked)])
    return picked[:n]


def uniform_sample(runs: list[Run], n: int, seed: int = 0) -> list[Run]:
    """対照の一様サンプリング。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    order = rng.permutation(len(runs))
    return [runs[i] for i in order[:n]]


def review(auto_approve: bool = True) -> dict[str, Any]:
    """起草済みテストの承認（CLI）。デモ用に自動承認を許す（結果ページに明記する）。"""
    from agenteval.drift.draft import load_drafts, register_draft

    drafts = load_drafts()
    approved = []
    for draft in drafts:
        if auto_approve:
            register_draft(draft)
            approved.append(draft["id"])
    return {"drafts": len(drafts), "approved": approved, "auto_approve": auto_approve}
