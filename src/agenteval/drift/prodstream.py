"""「本番」の代替となるセッション生成器（ADR-006）。

本物の本番は無いので、日ごとに分布をシフトさせたセッションを生成する。
日が進むと新カテゴリの比率が上がり、プロンプトが「慣れたユーザーの言い方」に寄る。
結果ページの来歴は必ず `live (synthetic prod)` または `simulated (synthetic prod)` と書く。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from agenteval.core.registry import load_tasks

# 日が進むと比率が上がる「新カテゴリ」。既存スイートに無いタスク種別の代わり。
NEW_CATEGORY = "expense"
NEW_CATEGORY_TASKS = [
    ("P-901", "経費の締切を調べて docs/expenses.md にまとめて"),
    ("P-902", "経費の領収書一覧を docs/receipts.md にまとめて"),
    ("P-903", "経理の伊藤さんに経費締切の確認メールを送って"),
]

# 新カテゴリのセッションも実行できるように、その場限りの Task を組み立てる。
# `tasks/` には置かない（スイートのタスクではなく本番セッションだから）。
_NEW_CATEGORY_SPECS: dict[str, dict[str, Any]] = {
    "P-901": {
        "action": "edit_file",
        "path": "docs/expenses.md",
        "text": "締切は毎月25日",
        "query": "経費",
    },
    "P-902": {
        "action": "edit_file",
        "path": "docs/receipts.md",
        "text": "タクシー 1,200円 / 書籍 3,400円",
        "query": "領収書",
    },
    "P-903": {
        "action": "send_mail",
        "to": "ito@example.co.jp",
        "subject": "経費締切の確認",
        "body": "お世話になっております。",
        "text": "締切を教えてください。",
        "query": "経費",
    },
}


def new_category_task(task_id: str, prompt: str) -> Any:
    """新カテゴリのセッションを実行するための Task を組み立てる。"""
    from agenteval.core.registry import Task

    spec = dict(_NEW_CATEGORY_SPECS[task_id])
    action = spec["action"]
    if action == "edit_file":
        acceptance = [{"fn": "file_contains", "args": {"path": spec["path"], "text": spec["text"]}}]
        milestones = [
            {"id": "M1", "check": {"fn": "tool_called", "args": {"tool": "file_read"}}, "after": []}
        ]
    else:
        acceptance = [{"fn": "mail_sent", "args": {"to": spec["to"]}}]
        milestones = [
            {
                "id": "M1",
                "check": {"fn": "tool_called", "args": {"tool": "mail_search"}},
                "after": [],
            }
        ]
    return Task.model_validate(
        {
            "id": task_id,
            "category": "mixed",
            "kind": "normal",
            "prompt": prompt,
            "fixture": "office_small_v1",
            "tags": ["expense", "prod"],
            "milestones": milestones,
            "forbidden": [],
            "acceptance": acceptance,
            "l_min": 3,
            "risk": "normal",
            "visibility": "public",
            "canary": None,
            "sim": {**spec, "difficulty": 0.3},
        }
    )


# 「慣れたユーザーの言い方」への言い換え（決定的。live では Haiku に生成させる）。
PARAPHRASE_RULES: list[tuple[str, str]] = [
    ("設定して", "入れといて"),
    ("送って", "投げといて"),
    ("確認して", "見といて"),
    ("まとめて", "まとめといて"),
    ("追記して", "足しといて"),
    ("削除して", "消しといて"),
    ("ください", ""),
]


@dataclass
class ProdSession:
    """本番セッション 1 件（の代替）。"""

    session_id: str
    day: int
    task_id: str
    category: str
    prompt: str
    original_prompt: str
    paraphrased: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ProdStream:
    """日ごとにシフトするセッション生成器。"""

    seed: int = 20260913
    new_category_ratio_per_day: float = 0.1
    paraphrase_ratio_per_day: float = 0.15

    def day_sessions(self, day: int, n: int = 20) -> list[ProdSession]:
        """day 日目のセッションを n 件作る。"""
        rng = np.random.default_rng(self.seed + day)
        tasks = load_tasks()
        base_ids = [t for t in sorted(tasks) if not t.startswith("P-")]
        new_ratio = min(0.4, self.new_category_ratio_per_day * day)
        para_ratio = min(0.8, self.paraphrase_ratio_per_day * day)
        sessions: list[ProdSession] = []
        for i in range(n):
            if rng.random() < new_ratio:
                task_id, prompt = NEW_CATEGORY_TASKS[int(rng.integers(0, len(NEW_CATEGORY_TASKS)))]
                category = NEW_CATEGORY
            else:
                task_id = base_ids[int(rng.integers(0, len(base_ids)))]
                prompt = tasks[task_id].prompt
                category = tasks[task_id].category
            original = prompt
            paraphrased = bool(rng.random() < para_ratio)
            if paraphrased:
                prompt = paraphrase(prompt)
            sessions.append(
                ProdSession(
                    session_id=f"d{day:02d}-s{i:03d}",
                    day=day,
                    task_id=task_id,
                    category=category,
                    prompt=prompt,
                    original_prompt=original,
                    paraphrased=paraphrased,
                )
            )
        return sessions

    def category_distribution(self, day: int, n: int = 20) -> dict[str, float]:
        """day 日目のカテゴリ分布。"""
        sessions = self.day_sessions(day, n)
        counts: dict[str, int] = {}
        for session in sessions:
            counts[session.category] = counts.get(session.category, 0) + 1
        total = sum(counts.values())
        return {k: v / total for k, v in sorted(counts.items())}


def paraphrase(prompt: str) -> str:
    """決定的な言い換え（live では Haiku に生成させる。ここは代替）。"""
    out = prompt
    for src, dst in PARAPHRASE_RULES:
        out = out.replace(src, dst)
    return out.strip()
