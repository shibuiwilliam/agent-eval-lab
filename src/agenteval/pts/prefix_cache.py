"""接頭辞キャッシュと分岐再実行（原典 3.6）。

旧版の軌跡のチェックポイント列を新版で辿り直し、行動が一致する間は判断確認だけを行う。
不一致になったステップでスナップショットを restore し、そこから通常ループで最後まで走る。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenteval.agent.context import apply_strategy
from agenteval.agent.prompts import build_system, build_user_message
from agenteval.core.ids import branch_run_id
from agenteval.core.normalize import normalize_args
from agenteval.core.registry import Task, Version
from agenteval.core.schema import Run
from agenteval.env.tools import tool_schemas
from agenteval.llm.cassette import CassetteMiss
from agenteval.llm.client import LLMClient, MessageRequest, Mode
from agenteval.llm.simulator import Simulator


@dataclass
class Checkpoint:
    """ステップ i 開始時点の会話と環境。"""

    run_id: str
    step_i: int
    messages_prefix: list[dict[str, Any]]
    snapshot_ref: str
    action_norm: str


@dataclass
class BranchResult:
    """分岐再実行 1 件の結果。"""

    run: Run
    divergence_step: int | None
    live_steps: int
    base_steps: int
    confirmations: int
    equivalence: str = "exact"

    @property
    def live_step_ratio(self) -> float:
        return round(self.live_steps / max(1, self.base_steps), 4)


def rebuild_messages(task: Task, run: Run, upto: int) -> list[dict[str, Any]]:
    """trace からステップ `upto` 開始時点の会話を復元する。"""
    messages: list[dict[str, Any]] = [build_user_message(task)]
    for step in run.steps[:upto]:
        assistant: list[dict[str, Any]] = []
        if step.assistant_text:
            assistant.append({"type": "text", "text": step.assistant_text})
        for call in step.tool_calls:
            assistant.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
            )
        messages.append({"role": "assistant", "content": assistant})
        results = []
        for result in step.tool_results:
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.id,
                    "content": [{"type": "text", "text": result.content}],
                    "is_error": result.is_error,
                }
            )
        if results:
            messages.append({"role": "user", "content": results})
    return messages


def checkpoints(task: Task, run: Run) -> list[Checkpoint]:
    """run の各ステップのチェックポイント列。"""
    out = []
    for step in run.steps:
        action = ""
        if step.tool_calls:
            call = step.tool_calls[0]
            action = f"{call.name}:{normalize_args(call.args)}"
        out.append(
            Checkpoint(
                run_id=run.run_id,
                step_i=step.i,
                messages_prefix=rebuild_messages(task, run, step.i),
                snapshot_ref=step.snapshot_ref,
                action_norm=action,
            )
        )
    return out


def action_of(step_calls: list[dict[str, Any]]) -> str:
    """応答のツール呼び出しを同値類キーにする（ツール名 ＋ 正規化引数）。"""
    if not step_calls:
        return "__no_tool__"
    call = step_calls[0]
    return f"{call['name']}:{normalize_args(call.get('input') or {})}"


@dataclass
class BranchRunner:
    """旧軌跡から新版を分岐再実行する。"""

    mode: Mode = "sim"
    seed: int = 20260913
    semantic_equiv: bool = False
    manifest: dict[str, Any] = field(default_factory=dict)

    def run(self, task: Task, new_version: Version, base_run: Run) -> BranchResult:
        """新版で旧軌跡を辿り直し、分岐点以降を実行する。"""
        from agenteval.agent.loop import RunOptions, run_task

        client = LLMClient(
            mode=self.mode,
            run_id=branch_run_id(base_run.run_id, new_version.id),
            simulator=Simulator(task=task, seed=self.seed, repeat=base_run.repeat),
        )
        system = build_system(new_version, task)
        schemas = tool_schemas(new_version.toolset)
        divergence: int | None = None
        confirmations = 0
        messages: list[dict[str, Any]] = []

        for step in base_run.steps:
            messages = rebuild_messages(task, base_run, step.i)
            send = apply_strategy(
                messages, new_version.config.context_strategy, new_version.config.summarize_after
            )
            request = MessageRequest(
                model=new_version.model_id(),
                system=system,
                messages=send,
                tools=schemas,
                max_tokens=new_version.config.max_tokens,
            )
            try:
                result = client.create(request, step=step.i)
            except CassetteMiss:
                # 旧軌跡から分岐した、という信号（握りつぶさない）
                divergence = step.i
                break
            confirmations += 1
            new_action = action_of(result.tool_uses())
            old_action = (
                f"{step.tool_calls[0].name}:{normalize_args(step.tool_calls[0].args)}"
                if step.tool_calls
                else "__no_tool__"
            )
            if new_action != old_action:
                divergence = step.i
                break

        self.manifest = {
            "base_run": base_run.run_id,
            "new_version": new_version.id,
            "equivalence": "semantic" if self.semantic_equiv else "exact",
            "divergence_step": divergence,
            "confirmations": confirmations,
        }

        if divergence is None:
            # 最後まで一致した: 旧軌跡の結果をそのまま新版の結果とみなす
            branched = base_run.model_copy(deep=True)
            branched.run_id = branch_run_id(base_run.run_id, new_version.id)
            branched.version_id = new_version.id
            branched.version_hash = new_version.hash()
            branched.mode = "branch"
            branched.parent_run_id = base_run.run_id
            branched.divergence_step = None
            return BranchResult(
                run=branched,
                divergence_step=None,
                live_steps=confirmations,
                base_steps=base_run.n_steps,
                confirmations=confirmations,
                equivalence=self.manifest["equivalence"],
            )

        options = RunOptions(
            mode=self.mode,
            seed=self.seed,
            repeat=base_run.repeat,
            resume_from=divergence,
            messages_override=rebuild_messages(task, base_run, divergence),
            parent_run=base_run,
            run_id_override=branch_run_id(base_run.run_id, new_version.id),
        )
        branched = run_task(task, new_version, options)
        branched.mode = "branch"
        live_steps = confirmations + max(0, branched.n_steps - divergence)
        return BranchResult(
            run=branched,
            divergence_step=divergence,
            live_steps=live_steps,
            base_steps=base_run.n_steps,
            confirmations=confirmations,
            equivalence=self.manifest["equivalence"],
        )


def validate_branching(
    pairs: list[tuple[Task, Version, Run, Run]],
) -> dict[str, Any]:
    """妥当性検査: 分岐再実行と通常実行の `outcome.passed` 一致率と分岐点の分布。

    `pairs` は (task, version, 分岐再実行の run, 通常実行の run)。
    """
    agree = 0
    divergences: list[int] = []
    rows = []
    for task, version, branched, normal in pairs:
        same = branched.passed() == normal.passed()
        agree += int(same)
        if branched.divergence_step is not None:
            divergences.append(branched.divergence_step)
        rows.append(
            {
                "task": task.id,
                "version": version.id,
                "branch_passed": branched.passed(),
                "normal_passed": normal.passed(),
                "agree": same,
                "divergence_step": branched.divergence_step,
            }
        )
    return {
        "n": len(pairs),
        "agreement": round(agree / len(pairs), 4) if pairs else 0.0,
        "median_divergence": float(sorted(divergences)[len(divergences) // 2])
        if divergences
        else None,
        "divergences": divergences,
        "rows": rows,
    }


def snapshot_exists(run: Run, step: int) -> bool:
    """分岐に必要なスナップショットが残っているか。"""
    for s in run.steps:
        if s.i == step:
            return bool(s.snapshot_ref) and Path(s.snapshot_ref).exists()
    return False
