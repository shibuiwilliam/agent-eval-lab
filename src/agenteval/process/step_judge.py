"""ステップ単位判定（原典 4.6）。ルーブリック判定と参照方策一致 `A(τ)`。

入力は「状態の要約（直近 k ステップのイベント ＋ 環境 diff）＋ 当該行動」。
自己申告文は入力から除外する（5.2 の主張と証拠の分離）。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from agenteval.core.normalize import action_key, normalize_args
from agenteval.core.registry import Task, Version
from agenteval.core.schema import Run
from agenteval.judge.rubric import STEP_RUBRIC, offline_step_judge
from agenteval.judge.verdict import Verdict, ask
from agenteval.llm.client import LLMClient

MutationKind = Literal["wrong_tool", "wrong_args", "destructive"]

EXPECTED_BY_ACTION: dict[str, set[str]] = {
    "create_event": {"calendar_search", "calendar_create", "checks_run", "finish"},
    "delete_event": {"calendar_search", "calendar_delete", "checks_run", "finish"},
    "send_mail": {"mail_search", "mail_send", "checks_run", "finish"},
    "edit_file": {"file_read", "file_write", "checks_run", "finish"},
    "delete_file": {"file_read", "file_backup", "file_delete", "checks_run", "finish"},
    "backup_file": {"file_read", "file_backup", "checks_run", "finish"},
    "lookup_cost": {"external_lookup", "mail_send", "file_write", "checks_run", "finish"},
    "do_nothing": {"calendar_search", "mail_search", "finish"},
    "ask": {"mail_search", "finish"},
    "recall": {"mail_search", "file_read", "finish"},
}


@dataclass
class StepSample:
    """判定対象のステップ 1 個。"""

    run_id: str
    task_id: str
    step: int
    tool: str
    args: dict[str, Any]
    context: dict[str, Any]
    mutated: MutationKind | None = None

    def payload(self) -> str:
        """ジャッジに渡す JSON（自己申告文は含めない）。"""
        return json.dumps(
            {
                "context": self.context,
                "action": {
                    "tool": self.tool,
                    "args": self.args,
                    "duplicate": self.context.get("duplicate", False),
                    "bad_args": self.context.get("bad_args", False),
                    "out_of_scope": self.context.get("out_of_scope", False),
                },
            },
            ensure_ascii=False,
        )


def extract_steps(task: Task, run: Run, k: int = 3) -> list[StepSample]:
    """run から判定対象ステップを作る。"""
    samples: list[StepSample] = []
    seen: Counter[str] = Counter()
    for step in run.steps:
        for call in step.tool_calls:
            key = action_key(call.name, call.args_norm)
            seen[key] += 1
            context = {
                "tools_called": [c.name for s in run.steps[: step.i] for c in s.tool_calls],
                "recent_events": [
                    {"tool": c.name, "args": c.args_norm}
                    for s in run.steps[max(0, step.i - k) : step.i]
                    for c in s.tool_calls
                ],
                "expected_tools": sorted(EXPECTED_BY_ACTION.get(task.sim.action, set())),
                "state_changed": step.state_hash_before != step.state_hash_after,
                "duplicate": seen[key] > 1,
            }
            samples.append(
                StepSample(
                    run_id=run.run_id,
                    task_id=run.task_id,
                    step=step.i,
                    tool=call.name,
                    args=call.args_norm,
                    context=context,
                )
            )
    return samples


def mutate(sample: StepSample, kind: MutationKind) -> StepSample:
    """行動を変異させた合成ステップを作る（検証用）。"""
    mutated = StepSample(
        run_id=sample.run_id,
        task_id=sample.task_id,
        step=sample.step,
        tool=sample.tool,
        args=dict(sample.args),
        context=dict(sample.context),
        mutated=kind,
    )
    if kind == "wrong_tool":
        mutated.tool = "mail_search" if sample.tool != "mail_search" else "calendar_search"
        if mutated.tool in mutated.context.get("expected_tools", []):
            mutated.tool = "external_lookup"
        mutated.args = {"query": "無関係な検索"}
    elif kind == "wrong_args":
        mutated.args = {k: "____" for k in sample.args} or {"path": "____"}
        mutated.context["bad_args"] = True
    else:
        mutated.tool = "file_delete"
        mutated.args = {"path": "docs/spec.md"}
        mutated.context["out_of_scope"] = True
    return mutated


def judge_step(sample: StepSample, client: LLMClient | None = None) -> Verdict:
    """ルーブリック判定を 1 件行う。"""
    return ask(client, STEP_RUBRIC, sample.payload(), offline_step_judge)


@dataclass
class ReferenceAgreement:
    """参照方策一致 `A(τ)`。"""

    mean: float
    minimum: float
    per_step: list[float] = field(default_factory=list)


def reference_agreement(
    task: Task,
    version: Version,
    run: Run,
    k: int = 5,
    client: LLMClient | None = None,
) -> ReferenceAgreement:
    """同じ接頭辞を参照方策に K 回投げ、行動の同値類の一致率を出す。

    live が無い場合は、シミュレータを参照方策として使う（能力の高い版の設定で引く）。
    """
    from agenteval.agent.prompts import build_system
    from agenteval.env.tools import tool_schemas
    from agenteval.llm.client import MessageRequest
    from agenteval.llm.models import model_id
    from agenteval.llm.simulator import Simulator
    from agenteval.pts.prefix_cache import rebuild_messages

    rates: list[float] = []
    system = build_system(version, task)
    schemas = tool_schemas(version.toolset)
    for step in run.steps:
        if not step.tool_calls:
            continue
        actual = f"{step.tool_calls[0].name}:{normalize_args(step.tool_calls[0].args)}"
        messages = rebuild_messages(task, run, step.i)
        samples: list[str] = []
        for trial in range(k):
            if client is None or client.mode == "sim":
                simulator = Simulator(task=task, seed=1000 + trial, repeat=trial)
                result = simulator.respond(
                    MessageRequest(
                        model=model_id("ref"),
                        system=system,
                        messages=messages,
                        tools=schemas,
                        max_tokens=version.config.max_tokens,
                    )
                )
            else:
                result = client.create(
                    MessageRequest(
                        model=model_id("ref"),
                        system=system,
                        messages=messages,
                        tools=schemas,
                        max_tokens=version.config.max_tokens,
                    )
                )
            uses = result.tool_uses()
            samples.append(
                f"{uses[0]['name']}:{normalize_args(uses[0].get('input') or {})}"
                if uses
                else "__no_tool__"
            )
        rates.append(sum(1 for s in samples if s == actual) / k)
    if not rates:
        return ReferenceAgreement(mean=0.0, minimum=0.0, per_step=[])
    return ReferenceAgreement(
        mean=round(sum(rates) / len(rates), 4), minimum=round(min(rates), 4), per_step=rates
    )
