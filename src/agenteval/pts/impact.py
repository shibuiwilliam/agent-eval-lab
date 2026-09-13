"""意味的影響推定（原典 3.3）。

プロンプト差分 → 影響タグの列挙 → タスクの tags と TF-IDF 類似で照合。
タグ列挙は Haiku の構造化出力で行うが、live が使えない場合は決定的な代替
（差分テキストからのキーワード抽出）を使い、来歴を `simulated` として返す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agenteval.core.registry import get_version, load_tasks
from agenteval.core.schema import Change
from agenteval.llm.client import LLMClient, MessageRequest
from agenteval.llm.models import model_id

IMPACT_TOOL = {
    "name": "submit_impact",
    "description": (
        "プロンプト差分がどの種類のタスクに影響するかを列挙する。"
        "tags には影響を受けるタスクの特徴語（日本語または英語の短い語）を 3〜8 個入れる。"
        "reason には判断の根拠を 1 文で書く。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["tags", "reason"],
    },
}

# 差分本文 → 影響タグの決定的な対応（live が無いときの代替）。
KEYWORD_TAGS: dict[str, list[str]] = {
    "日付": ["date", "calendar_create", "scheduling"],
    "YYYY": ["date", "calendar_create", "scheduling"],
    "検査": ["checks_run", "verification"],
    "checks_run": ["checks_run", "verification"],
    "バックアップ": ["file_backup", "file_delete"],
    "削除": ["file_delete", "file_backup"],
    "メール": ["mail_send", "contacts"],
    "計画": ["submit_plan", "plan"],
    "要約": ["context", "niah"],
    "口調": [],
    "丁寧": [],
    "簡潔": ["efficiency"],
}


@dataclass
class ImpactEstimate:
    """影響推定の結果。"""

    tags: list[str]
    reason: str
    provenance: str
    selected: list[str]
    selected_ratio: float


def diff_text(change: Change) -> str:
    """変更された section の新旧本文を並べたテキスト。"""
    base = get_version(change.base).section_bodies() if change.base != "synthetic" else {}
    target = get_version(change.target).section_bodies() if change.target != "synthetic" else {}
    parts = []
    for name in sorted(change.components):
        parts.append(
            f"[section: {name}]\n- 旧: {base.get(name, '(無し)')}\n- 新: {target.get(name, '(無し)')}"
        )
    return "\n\n".join(parts)


def tags_offline(text: str) -> tuple[list[str], str]:
    """決定的なタグ抽出（来歴 simulated）。"""
    tags: list[str] = []
    for keyword, mapped in KEYWORD_TAGS.items():
        if keyword in text:
            tags.extend(mapped)
    return sorted(set(tags)), "差分本文のキーワードから決定的に抽出した（LLM 不使用）"


def tags_live(change: Change, client: LLMClient) -> tuple[list[str], str]:
    """Haiku に構造化出力でタグを列挙させる。"""
    request = MessageRequest(
        model=model_id("agent"),
        system=[{"type": "text", "text": "あなたはテスト選択の補助をする分析器です。"}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "次のシステムプロンプト差分が影響するタスクの特徴を列挙してください。\n\n"
                        + diff_text(change),
                    }
                ],
            }
        ],
        tools=[IMPACT_TOOL],
        tool_choice={"type": "tool", "name": "submit_impact"},
        max_tokens=512,
    )
    result = client.create(request)
    uses = result.tool_uses()
    if not uses:
        return [], "ツール出力が得られなかった"
    payload: dict[str, Any] = uses[0].get("input") or {}
    return [str(t) for t in payload.get("tags", [])], str(payload.get("reason", ""))


def similarity_scores(tags: list[str]) -> dict[str, float]:
    """タグとタスク tags の TF-IDF コサイン類似。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    tasks = load_tasks()
    task_ids = sorted(tasks)
    documents = [" ".join(tasks[t].tags + [tasks[t].category, tasks[t].kind]) for t in task_ids]
    query = " ".join(tags) or "none"
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
    matrix = vectorizer.fit_transform([*documents, query])
    sims = cosine_similarity(matrix[-1], matrix[:-1])[0]
    return {task_id: float(score) for task_id, score in zip(task_ids, sims, strict=True)}


def estimate(
    change: Change, client: LLMClient | None = None, threshold: float = 0.15
) -> ImpactEstimate:
    """影響タグを列挙し、閾値以上のタスクを選ぶ。"""
    text = diff_text(change)
    if client is not None and client.mode in ("auto", "record", "replay"):
        tags, reason = tags_live(change, client)
        provenance = "live" if client.mode in ("auto", "record") else "replay"
    else:
        tags, reason = tags_offline(text)
        provenance = "simulated"
    scores = similarity_scores(tags)
    selected = sorted([t for t, s in scores.items() if s >= threshold])
    return ImpactEstimate(
        tags=tags,
        reason=reason,
        provenance=provenance,
        selected=selected,
        selected_ratio=round(len(selected) / len(scores), 4) if scores else 0.0,
    )


def dump(estimate_result: ImpactEstimate) -> str:
    return json.dumps(estimate_result.__dict__, ensure_ascii=False)
