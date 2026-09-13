"""エージェントループ。SDK の tool runner は使わず `messages.create` を自前で回す（ADR-001）。

ステップ境界で会話と環境を止め、スナップショットを取る。分岐再実行（3.6）と
アブレーション（4.3）はこの境界を使う。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agenteval.agent.context import apply_strategy, estimate_tokens
from agenteval.agent.prompts import build_system, build_user_message
from agenteval.core.events import to_events
from agenteval.core.ids import run_id as make_run_id
from agenteval.core.normalize import normalize_args
from agenteval.core.registry import Task, Version
from agenteval.core.schema import Cost, Outcome, Run, Step, ToolCall, ToolResult, Usage
from agenteval.env import tools as tools_mod
from agenteval.env.checks import CheckContext, check_label, run_check
from agenteval.env.fixtures import load_sql
from agenteval.env.injectors import DriftInjector, FaultInjector, NoiseInjector
from agenteval.env.office import OfficeEnv, SnapshotRef, dump_snapshot
from agenteval.llm.client import LLMClient, MessageRequest, Mode
from agenteval.llm.cost import REPO_ROOT, BudgetExceeded, load_pricing
from agenteval.llm.simulator import Simulator
from agenteval.process.linter import lint
from agenteval.process.milestones import evaluate as evaluate_milestones
from agenteval.process.rules.global_rules import build_rules

WORK_DIR = REPO_ROOT / "data" / "work"
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots"


@dataclass
class RunOptions:
    """1 run の実行条件。"""

    mode: Mode = "sim"
    seed: int = 20260913
    repeat: int = 0
    fault: FaultInjector | None = None
    noise: NoiseInjector | None = None
    drift: DriftInjector | None = None
    external_version: str = "v1"
    client: LLMClient | None = None
    messages_override: list[dict[str, Any]] | None = None
    resume_from: int | None = None
    parent_run: Run | None = None
    run_id_override: str | None = None
    keep_snapshots: bool = True
    states: list[dict[str, Any]] = field(default_factory=list)


def run_task(task: Task, version: Version, opts: RunOptions | None = None) -> Run:
    """1 タスク 1 版 1 回を実行して `Run` を返す。"""
    options = opts or RunOptions()
    rid = options.run_id_override or make_run_id(
        task.id, version.id, options.seed, options.repeat, options.mode
    )
    env = _make_env(task, rid, options)
    initial_hash = env.state_hash()
    client = options.client or LLMClient(
        mode=options.mode,
        run_id=rid,
        simulator=Simulator(
            task=task, seed=options.seed, repeat=options.repeat, max_steps=version.config.max_steps
        ),
    )
    system = build_system(version, task)
    schemas = tools_mod.tool_schemas(version.toolset)
    schemas[-1] = {**schemas[-1], "cache_control": {"type": "ephemeral"}}
    messages: list[dict[str, Any]] = (
        [dict(m) for m in options.messages_override]
        if options.messages_override
        else [build_user_message(task)]
    )
    start_step = options.resume_from or 0

    run = Run(
        run_id=rid,
        task_id=task.id,
        version_id=version.id,
        version_hash=version.hash(),
        mode="sim" if options.mode == "sim" else ("replay" if options.mode == "replay" else "live"),
        seed=options.seed,
        repeat=options.repeat,
        provenance="simulated"
        if options.mode == "sim"
        else ("replay" if options.mode == "replay" else "live"),
    )
    injections = [
        i.to_dict()
        for inj in (options.fault, options.noise, options.drift)
        if inj
        for i in inj.records
    ]
    started = time.monotonic()
    states: list[dict[str, Any]] = []
    finished = False
    plan_pending = version.config.plan_first and start_step == 0

    if options.parent_run is not None and start_step > 0:
        parent = options.parent_run
        run.parent_run_id = parent.run_id
        run.divergence_step = start_step
        run.steps = [s.model_copy(deep=True) for s in parent.steps[:start_step]]
        states = _states_from_snapshots(parent, start_step)
        ref = snapshot_path(parent, start_step - 1)
        if ref is not None and ref.exists():
            env.restore(SnapshotRef(run_id=parent.run_id, step=start_step - 1, path=ref))

    for i in range(start_step, version.config.max_steps):
        send = apply_strategy(
            messages, version.config.context_strategy, version.config.summarize_after
        )
        request = MessageRequest(
            model=version.model_id(),
            system=system,
            messages=send,
            tools=schemas,
            tool_choice={"type": "tool", "name": "submit_plan"} if plan_pending else None,
            max_tokens=version.config.max_tokens,
        )
        plan_pending = False
        state_before = env.state_hash()
        t0 = time.monotonic()
        try:
            result = client.create(request, step=i)
        except BudgetExceeded as exc:
            run.final_text = f"[予算停止] {exc}"
            break
        latency_ms = int((time.monotonic() - t0) * 1000)

        assistant_content = [dict(b) for b in result.content]
        messages.append({"role": "assistant", "content": assistant_content})
        tool_uses = result.tool_uses()
        step = Step(
            i=i,
            state_hash_before=state_before,
            state_hash_after=state_before,
            assistant_text=result.text(),
            usage=result.usage,
            latency_ms=latency_ms,
            context_tokens=estimate_tokens(send, system),
        )

        if not tool_uses:
            # ツール無しの end_turn = 主張なしの完了（リンターが検出する）
            run.final_text = result.text()
            step.state_hash_after = env.state_hash()
            run.steps.append(step)
            states.append(env.dump())
            break

        result_blocks: list[dict[str, Any]] = []
        for block in tool_uses:
            call_args = dict(block.get("input") or {})
            step.tool_calls.append(
                ToolCall(
                    id=block["id"],
                    name=block["name"],
                    args=call_args,
                    args_norm=normalize_args(call_args),
                )
            )
            payload, is_error = _execute_tool(env, block["name"], call_args, version, options, i)
            content = json.dumps(payload, ensure_ascii=False)
            step.tool_results.append(
                ToolResult(
                    id=block["id"],
                    content=content,
                    is_error=is_error,
                    bytes=len(content),
                    injected=_injection_note(options, i, block["name"]),
                )
            )
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": [{"type": "text", "text": content}],
                    "is_error": is_error,
                }
            )
            if block["name"] == "finish":
                run.final_text = str(call_args.get("summary", ""))
                run.claims = [str(c) for c in (call_args.get("claims") or [])]
                finished = True
            if block["name"] == "submit_plan":
                run.plan = [dict(s) for s in (call_args.get("steps") or [])]

        messages.append({"role": "user", "content": result_blocks})
        step.state_hash_after = env.state_hash()
        if options.keep_snapshots:
            step.snapshot_ref = env.snapshot(i).as_str()
        run.steps.append(step)
        states.append(env.dump())
        if finished:
            break

    run.wall_time_s = round(time.monotonic() - started, 3)
    run.injections = (
        injections
        + [
            i.to_dict()
            for inj in (options.fault, options.noise, options.drift)
            if inj
            for i in inj.records
        ][len(injections) :]
    )
    run.cost = _cost_of(run, version)
    run.outcome = evaluate_outcome(task, run, env, states, initial_hash)
    env.close()
    return run


def evaluate_outcome(
    task: Task,
    run: Run,
    env: OfficeEnv,
    states: list[dict[str, Any]],
    initial_hash: str,
) -> Outcome:
    """acceptance・マイルストーン・リンターを評価する。"""
    final_state = states[-1] if states else env.dump()
    ctx = CheckContext(run=run, state=final_state, initial_state_hash=initial_hash)
    acceptance = {check_label(spec): run_check(ctx, spec) for spec in task.acceptance}
    milestones = evaluate_milestones(task, run, states, initial_hash)
    violations = lint(to_events(run), build_rules(task))
    return Outcome(
        passed=bool(acceptance) and all(acceptance.values()),
        acceptance_results=acceptance,
        milestone_score=milestones.score,
        milestone_steps=milestones.first_step,
        linter_violations=violations,
    )


def _make_env(task: Task, rid: str, options: RunOptions) -> OfficeEnv:
    from agenteval.env.external import ExternalService

    db_path = WORK_DIR / f"{rid}.sqlite"
    env = OfficeEnv.create(
        db_path,
        load_sql(task.fixture),
        run_id=rid,
        snapshot_dir=SNAPSHOT_DIR,
    )
    env.external = ExternalService(version=options.external_version)  # type: ignore[arg-type]
    if options.drift is not None and options.drift.external_version:
        options.drift.bump_external(env, options.drift.external_version)
    return env


def _execute_tool(
    env: OfficeEnv,
    name: str,
    args: dict[str, Any],
    version: Version,
    options: RunOptions,
    step: int,
) -> tuple[dict[str, Any], bool]:
    """ツールを実行し、注入器を通す。"""
    try:
        payload = tools_mod.execute(env, name, args, version.toolset)
        is_error = bool(payload.get("error"))
    except (ValidationError, tools_mod.ToolError, KeyError, ValueError) as exc:
        # 引数検証の失敗は tool_result のエラーとして返す（復帰行動の観察対象）
        payload = {"error": "invalid_arguments", "detail": str(exc)[:300]}
        is_error = True
    if options.fault is not None:
        payload, injected_error = options.fault.apply(step, name, payload)
        is_error = is_error or injected_error
    if options.noise is not None:
        payload = options.noise.apply(step, name, payload)
    return payload, is_error


def _injection_note(options: RunOptions, step: int, tool: str) -> dict[str, Any] | None:
    for injector in (options.fault, options.noise, options.drift):
        if injector is None:
            continue
        for record in injector.records:
            if record.step == step and record.tool == tool:
                return record.to_dict()
    return None


def _cost_of(run: Run, version: Version) -> Cost:
    """run のコスト集計。sim / replay は usd = 0。"""
    usage = Usage()
    for step in run.steps:
        usage.input_tokens += step.usage.input_tokens
        usage.output_tokens += step.usage.output_tokens
        usage.cache_read_input_tokens += step.usage.cache_read_input_tokens
        usage.cache_creation_input_tokens += step.usage.cache_creation_input_tokens
    usd = load_pricing().usd(version.model_id(), usage) if run.mode == "live" else 0.0
    return Cost(
        usd=round(usd, 6),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        calls=len(run.steps),
    )


def snapshot_path(run: Run, step: int) -> Path | None:
    """ステップ i のスナップショットへのパス。"""
    for s in run.steps:
        if s.i == step and s.snapshot_ref:
            return Path(s.snapshot_ref)
    return None


def _states_from_snapshots(run: Run, upto: int) -> list[dict[str, Any]]:
    """親 run のスナップショットからステップごとの状態を復元する（分岐再実行の前半部分）。"""
    states: list[dict[str, Any]] = []
    for step in run.steps[:upto]:
        if step.snapshot_ref and Path(step.snapshot_ref).exists():
            states.append(dump_snapshot(Path(step.snapshot_ref)))
        elif states:
            states.append(states[-1])
        else:
            states.append({})
    return states
