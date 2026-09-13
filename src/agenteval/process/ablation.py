"""アブレーションによる無駄呼び出し判定（原典 4.3）。

呼び出し i の結果を `[result omitted by ablation]` に置き換えた会話で、ステップ i 後の
スナップショットを restore してから i+1 以降を実行し、`outcome.passed` を比較する。
分岐再実行と同じ接頭辞キャッシュの基盤を使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agenteval.core.registry import Task, Version
from agenteval.core.schema import Run
from agenteval.llm.client import Mode
from agenteval.pts.prefix_cache import rebuild_messages

OMITTED = "[result omitted by ablation]"
SKIP_TOOLS = {"finish", "submit_plan"}


@dataclass
class CallRef:
    """アブレーション対象の呼び出し 1 件。"""

    step: int
    call_id: str
    tool: str
    args: dict[str, Any]


@dataclass
class AblationResult:
    """1 run のアブレーション結果。"""

    run_id: str
    task_id: str
    version_id: str
    total_calls: int
    wasted_calls: list[CallRef] = field(default_factory=list)
    useful_calls: list[CallRef] = field(default_factory=list)

    @property
    def waste_rate(self) -> float:
        """無駄呼び出し率 `U(τ)`。"""
        if not self.total_calls:
            return 0.0
        return round(len(self.wasted_calls) / self.total_calls, 4)


def targets(run: Run) -> list[CallRef]:
    """アブレーション対象（finish / submit_plan は除く）。"""
    out = []
    for step in run.steps:
        for call in step.tool_calls:
            if call.name in SKIP_TOOLS:
                continue
            out.append(CallRef(step=step.i, call_id=call.id, tool=call.name, args=call.args))
    return out


def messages_with_omission(task: Task, run: Run, upto: int, call_id: str) -> list[dict[str, Any]]:
    """ステップ `upto` 開始時点の会話のうち、指定呼び出しの結果だけを伏せる。"""
    messages = rebuild_messages(task, run, upto)
    for message in messages:
        for block in message.get("content", []):
            if block.get("type") == "tool_result" and block.get("tool_use_id") == call_id:
                block["content"] = [{"type": "text", "text": OMITTED}]
    return messages


def ablate_run(task: Task, version: Version, run: Run, mode: Mode = "sim") -> AblationResult:
    """1 run の全呼び出しをアブレーションする。"""
    from agenteval.agent.loop import RunOptions, run_task

    result = AblationResult(
        run_id=run.run_id, task_id=run.task_id, version_id=run.version_id, total_calls=0
    )
    refs = targets(run)
    result.total_calls = len(refs)
    for ref in refs:
        resume = ref.step + 1
        if resume >= run.n_steps:
            # 最終ステップの呼び出しは、以降の判断が無いので影響を測れない
            result.wasted_calls.append(ref)
            continue
        options = RunOptions(
            mode=mode,
            seed=run.seed,
            repeat=run.repeat,
            resume_from=resume,
            messages_override=messages_with_omission(task, run, resume, ref.call_id),
            parent_run=run,
            run_id_override=f"abl__{run.run_id}__{ref.call_id}",
            keep_snapshots=False,
        )
        replayed = run_task(task, version, options)
        if replayed.passed() == run.passed():
            result.wasted_calls.append(ref)
        else:
            result.useful_calls.append(ref)
    return result


def waste_rate_by_version(results: list[AblationResult]) -> dict[str, float]:
    """版ごとの `U`。"""
    groups: dict[str, list[AblationResult]] = {}
    for result in results:
        groups.setdefault(result.version_id, []).append(result)
    return {
        version: round(
            sum(len(r.wasted_calls) for r in items) / max(1, sum(r.total_calls for r in items)), 4
        )
        for version, items in sorted(groups.items())
    }


def surrogate_labels(task: Task, run: Run, client: Any | None = None) -> dict[str, bool]:
    """代理指標: ジャッジに「この結果は後続で参照されたか」を問う。True = 有用。"""
    import json

    from agenteval.judge.rubric import offline_ablation_judge
    from agenteval.judge.verdict import ask

    out: dict[str, bool] = {}
    all_calls = [(s, c) for s in run.steps for c in s.tool_calls]
    for index, (step, call) in enumerate(all_calls):
        if call.name in SKIP_TOOLS:
            continue
        results = {r.id: r for r in step.tool_results}
        payload = json.dumps(
            {
                "result": results[call.id].content if call.id in results else "",
                "following_actions": [
                    {"tool": c.name, "args": c.args_norm} for _, c in all_calls[index + 1 :]
                ],
            },
            ensure_ascii=False,
        )
        verdict = ask(client, "あなたは軌跡の評価者です。", payload, offline_ablation_judge)
        out[call.id] = verdict.score >= 4
    return out


def precision_recall(surrogate: dict[str, bool], ablation: AblationResult) -> dict[str, float]:
    """代理指標の precision / recall（アブレーション結果を正解とする）。

    正例 = 「有用」。
    """
    useful_ids = {c.call_id for c in ablation.useful_calls}
    wasted_ids = {c.call_id for c in ablation.wasted_calls}
    predicted_useful = {
        k for k, v in surrogate.items() if v and (k in useful_ids or k in wasted_ids)
    }
    tp = len(predicted_useful & useful_ids)
    fp = len(predicted_useful & wasted_ids)
    fn = len(useful_ids - predicted_useful)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }
