"""コーディング用のエージェントループ（docs/plan/coding/SPEC.md 11.2〜11.4 節、ADR-029・035・038・040、W1-I5）。

既存の `agent/loop.py`（オフィス用）は変えない。SDK の tool runner は使わず `messages.create` を自前で回す。
各ツール呼び出しについて、権限判定を 1 件記録し、効果を観測（ファイル差分・監査フック・起動記録・シンク走査）から
作って `Step` に残す。評価（隠しテストと元のテストでの最終評価）は `RepoEnv` の strict な評価に任せ、
ループ側で独自に合否を判定しない。

不変条件:
- すべてのツール呼び出しに `PolicyDecision` がちょうど 1 件ある（`skip_broker=True` の植込みツールを除く）。
  引数の検証に失敗した呼び出し・未知のツールも `deny`（`harness.invalid_arguments`）として 1 件記録する
- `BudgetExceeded` は飲み込まない。その時点までの Run を `.partial.json` に保存し、env を閉じて投げ直す（ADR-040）
- `history_cache` の有無でリクエストが変わるのは、送るメッセージの写しの最後のブロックの `cache_control` だけ
- 保存する会話履歴・trace には `cache_control` を入れない
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, Final, Protocol, cast, get_args

from pydantic import ValidationError

from agenteval.agent.context import estimate_tokens
from agenteval.agent.scripted import Script, ScriptedAgent
from agenteval.core.coding_registry import (
    coding_cassette_salt,
    coding_run_id,
    mode_dir_of,
    prompt_text,
    version_hash,
)
from agenteval.core.coding_store import save_coding_run, save_fingerprints
from agenteval.core.coding_types import (
    ACC_ASKED_USER,
    ACC_FINISHED,
    ACC_HIDDEN,
    ACC_VISIBLE,
    CODING_DATA_ROOT,
    INIT_STEP,
    REPO_FIXTURE_ROOT,
    CanaryId,
    CodingOutcome,
    CodingSnapshotRef,
    CodingTask,
    CodingVersion,
    ExecutorFactory,
    PermissionBroker,
    PermissionPolicyName,
    SnapshotManifest,
    ToolOutputSize,
)
from agenteval.core.normalize import normalize_args
from agenteval.core.schema import (
    Cost,
    Effect,
    Outcome,
    PolicyDecision,
    Provenance,
    Run,
    RunMode,
    Step,
    ToolCall,
    ToolResult,
    Usage,
)
from agenteval.env.effects import dedupe_effects, fs_diff_effects, sink_effects
from agenteval.env.repo import PathEscapeError, RepoEnv
from agenteval.env.repo_tools import (
    FinishArgs,
    ToolContext,
    ToolOutput,
    ToolSpec,
    build_toolset,
    schemas_of,
)
from agenteval.env.sandbox import SandboxError, docker_executor_factory
from agenteval.llm.client import LLMClient, MessageRequest, Mode
from agenteval.llm.cost import BudgetExceeded, BudgetGuard, load_pricing
from agenteval.security.canary import CanaryVault

# 評価（最終の可視テスト・隠しテスト）の時間上限。エージェントのツール呼び出し（tool_timeout_s）とは別
FINAL_EVAL_TIMEOUT_S: Final = 120.0
# ハーネス秘密カナリアを ToolContext.harness_env に渡すときの環境変数名（サンドボックスには渡さない）
HARNESS_SECRET_ENV: Final = "AGENTEVAL_HARNESS_SECRET"
CACHE_BREAKPOINT: Final = {"type": "ephemeral"}

SnapshotFn = Callable[[RepoEnv, int], CodingSnapshotRef]


class ToolFault(Protocol):
    """env/injectors.FaultInjector と同じ形（blocks / block / apply）。EC-O2 の植込み用。"""

    def blocks(self, step: int, tool: str) -> bool:
        """このステップでツールの実行そのものを止めるか。"""
        ...

    def block(self, step: int, tool: str) -> tuple[dict[str, Any], bool]:  # Any: ツール応答の JSON
        """ツールを実行せずに返す応答と is_error。"""
        ...

    def apply(
        self,
        step: int,
        tool: str,
        result: dict[str, Any],  # Any: ツール応答の JSON
    ) -> tuple[dict[str, Any], bool]:
        """成功した応答を加工する。"""
        ...


@dataclass
class CodingRunOptions:
    """1 run の実行条件（SPEC 11.2 節）。

    SPEC への追加: `fixture_root`（テストが自前のフィクスチャを渡す）と `require_expected`
    （最終評価で expected_nodeids.json が無い評価を不合格にするか。既定 True。コーパスの評価を
    「ノード集合の照合なし」で通さないため）。
    """

    mode: Mode = "sim"
    stage: str = "unit"  # 2.4 節の段。run_id に入る
    repeat: int = 0
    seed: int = 0  # 段の seed（2.4 節）。Run.seed に記録し、カセットの塩に入れる
    client: LLMClient | None = None
    script: Script | None = None  # 与えれば ScriptedAgent で走らせる（mode は "sim" に限る）
    executor_factory: ExecutorFactory | None = None  # None なら docker_executor_factory
    broker: PermissionBroker | None = None  # None なら version.harness.permission_policy から作る
    budget: BudgetGuard | None = None  # 実験単位の guard（experiment_budget）を共有させる
    vault: CanaryVault | None = None  # None なら新しく作る（鍵は保存しない）
    tool_overrides: Mapping[str, ToolSpec] | None = None  # EC-O2 の植込み（バグを模したツール）用
    fault: ToolFault | None = None  # EC-O2 の植込み（ADR-026 以前の注入器）用
    snapshotter: SnapshotFn | None = None  # EC-O2 の植込み（無言で落ちるフック）用
    keep_workdir: bool = False
    data_root: Path = CODING_DATA_ROOT
    fixture_root: Path = REPO_FIXTURE_ROOT
    require_expected: bool = True


# --- 権限ブローカー（SPEC 11.4 節。P2 は W1-I6 の security/policy.py） -----------------
class AllowAllBroker:
    """P0_allow_all: すべて allow。"""

    def decide(self, call: ToolCall, capability: str, task: CodingTask) -> PolicyDecision:
        return PolicyDecision(
            call_id=call.id, capability=capability, decision="allow", rule_id="P0.allow_all"
        )


class PathScopeBroker:
    """P1_path_scope: 作業ツリー外は deny、作業ツリー内の書き込みは allowed_paths に一致すれば allow。他は allow。"""

    def decide(self, call: ToolCall, capability: str, task: CodingTask) -> PolicyDecision:
        parts = capability.split(":", 3)
        if len(parts) == 4 and parts[0] == "fs":
            verb, zone, path = parts[1], parts[2], parts[3]
            if zone == "outside":
                return PolicyDecision(
                    call_id=call.id,
                    capability=capability,
                    decision="deny",
                    rule_id="P1.path_scope",
                    detail="作業ツリーの外",
                )
            if verb == "write" and zone == "repo":
                allowed = any(_path_matches(path, pattern) for pattern in task.allowed_paths)
                return PolicyDecision(
                    call_id=call.id,
                    capability=capability,
                    decision="allow" if allowed else "deny",
                    rule_id="P1.path_scope",
                    detail="" if allowed else "allowed_paths に一致しない",
                )
        return PolicyDecision(
            call_id=call.id, capability=capability, decision="allow", rule_id="P1.default_allow"
        )


def _path_matches(path: str, pattern: str) -> bool:
    return PurePosixPath(path).full_match(pattern)


def broker_for(policy: PermissionPolicyName) -> PermissionBroker:
    """方策名からブローカーを作る。P2 は security/policy.py（W1-I6）のブローカーを options.broker で渡す。"""
    if policy == "P0_allow_all":
        return AllowAllBroker()
    if policy == "P1_path_scope":
        return PathScopeBroker()
    raise ValueError(
        f"{policy} のブローカーは CodingRunOptions.broker で渡す（実装は security/policy.py）"
    )


# --- 本体 ------------------------------------------------------------------------------
def run_coding_task(
    task: CodingTask, version: CodingVersion, opts: CodingRunOptions | None = None
) -> tuple[Run, CodingOutcome]:
    """1 タスク 1 版 1 回を実行して (Run, CodingOutcome) を返し、data_root に保存する。"""
    options = opts or CodingRunOptions()
    return _CodingRun(task, version, options).execute()


class _CodingRun:
    """1 run の実行状態（run_coding_task の内部）。"""

    def __init__(self, task: CodingTask, version: CodingVersion, opts: CodingRunOptions) -> None:
        if opts.script is not None and opts.mode != "sim":
            raise ValueError("script を渡すときは mode='sim' に限る")
        if opts.script is not None and opts.client is not None:
            raise ValueError(
                "script と client は同時に指定できない（script は ScriptedAgent で走らせる）"
            )
        if opts.mode == "sim" and opts.script is None and opts.client is None:
            raise ValueError("mode='sim' には script か client が要る")
        self.task = task
        self.version = version
        self.opts = opts
        script_id = opts.script.script_id if opts.script is not None else None
        self.run_id = coding_run_id(task.id, version.id, opts.stage, opts.repeat, script_id)
        self.script_id = script_id
        self.run_mode: RunMode = (
            "sim" if opts.mode == "sim" else ("replay" if opts.mode == "replay" else "live")
        )
        self.mode_dir = mode_dir_of(self.run_mode)
        self.harness = version.harness
        self.toolset: dict[str, ToolSpec] = build_toolset(version.toolset)
        for name, spec in (opts.tool_overrides or {}).items():
            self.toolset[name] = spec
        self.broker: PermissionBroker = opts.broker or broker_for(self.harness.permission_policy)
        self.vault = opts.vault or CanaryVault()
        self.snapshotter: SnapshotFn = opts.snapshotter or _default_snapshot
        self.harness_env = {HARNESS_SECRET_ENV: self.vault.value(self.run_id, "harness_secret")}
        self.client = opts.client or self._make_client()
        provenance: Provenance = (
            "simulated"
            if self.run_mode == "sim"
            else ("replay" if self.run_mode == "replay" else "live")
        )
        self.run = Run(
            run_id=self.run_id,
            task_id=task.id,
            version_id=version.id,
            version_hash=version_hash(version),
            mode=self.run_mode,
            seed=opts.seed,
            repeat=opts.repeat,
            provenance=provenance,
            env_kind="repo",
            injections=self._injection_records(),
        )
        self.sizes: list[ToolOutputSize] = []
        self.finished = False
        self.asked_user = False
        self.fatal = False
        self.last_ref: CodingSnapshotRef | None = None
        self.env: RepoEnv
        self.last_scan: SnapshotManifest

    # --- 準備 ---------------------------------------------------------------------
    def _make_client(self) -> LLMClient:
        simulator = None
        if self.opts.script is not None:
            vault, run_id = self.vault, self.run_id

            def resolve(canary_id: str) -> str:
                if canary_id not in get_args(CanaryId):
                    raise ValueError(f"未知のカナリア ID: {canary_id!r}")
                return vault.value(run_id, cast(CanaryId, canary_id))

            simulator = ScriptedAgent(self.opts.script, resolver=resolve)
        return LLMClient(
            mode=self.opts.mode,
            run_id=self.run_id,
            budget=self.opts.budget,
            simulator=simulator,
            cassette_salt=coding_cassette_salt(
                self.task.id, self.version.id, self.opts.seed, self.opts.repeat
            ),
        )

    def _injection_records(self) -> list[dict[str, Any]]:  # Any: 注入の記録（JSON）
        """植込み（通常の run には無いもの）を run manifest に記録する。"""
        records: list[dict[str, Any]] = []
        for name, spec in (self.opts.tool_overrides or {}).items():
            records.append({"kind": "tool_override", "tool": name, "skip_broker": spec.skip_broker})
        if self.opts.fault is not None:
            records.append({"kind": "fault", "type": type(self.opts.fault).__name__})
        if self.opts.snapshotter is not None:
            records.append({"kind": "snapshotter_override"})
        if self.opts.broker is not None:
            records.append({"kind": "broker_override", "type": type(self.opts.broker).__name__})
        return records

    def execute(self) -> tuple[Run, CodingOutcome]:
        opts = self.opts
        factory = opts.executor_factory or docker_executor_factory(self.run_id, self.mode_dir)
        canaries: dict[str, str] = {
            str(cid): value for cid, value in self.vault.canary_values(self.run_id).items()
        }
        self.env = RepoEnv.create(
            self.task,
            self.run_id,
            executor_factory=factory,
            mode_dir=self.mode_dir,
            extra_files=self.vault.honeypot_files(self.run_id, self.task.honeypot),
            canaries=canaries,
            data_root=opts.data_root,
            fixture_root=opts.fixture_root,
        )
        try:
            save_fingerprints(
                self.run_id,
                self.mode_dir,
                self.vault.fingerprints(self.run_id),
                data_root=opts.data_root,
            )
            started = time.monotonic()
            self._loop(started)
            return self._finalize(started)
        finally:
            self.env.close(remove_workdir=not opts.keep_workdir)

    # --- ループ -------------------------------------------------------------------
    def _system(self) -> list[dict[str, Any]]:  # Any: Anthropic の system ブロック
        return [
            {"type": "text", "text": prompt_text(self.version), "cache_control": CACHE_BREAKPOINT}
        ]

    def _tools(self) -> list[dict[str, Any]]:  # Any: Anthropic の tools 定義
        schemas = schemas_of(self.toolset)
        schemas[-1] = {**schemas[-1], "cache_control": CACHE_BREAKPOINT}
        return schemas

    def _send_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:  # Any: メッセージ
        """送るメッセージの写し。history_cache なら最後のメッセージの最後のブロックに cache_control を付ける。

        保存する会話履歴（messages）は変えない。区切りは system・tools・ここの最大 3 個（上限 4 の内側）。
        """
        if not self.harness.history_cache:
            return list(messages)
        last = messages[-1]
        blocks = [dict(b) for b in last["content"]]
        blocks[-1] = {**blocks[-1], "cache_control": CACHE_BREAKPOINT}
        return [*messages[:-1], {**last, "content": blocks}]

    def _loop(self, started: float) -> None:
        run, env = self.run, self.env
        system = self._system()
        tools = self._tools()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": self.task.prompt}]}
        ]
        self.last_scan = env.scan()
        prev_hash = env.state_hash()
        for i in range(self.harness.max_steps):
            send = self._send_messages(messages)
            request = MessageRequest(
                model=_model_id(self.version),
                system=system,
                messages=send,
                tools=tools,
                max_tokens=self.harness.max_tokens,
            )
            t0 = time.monotonic()
            try:
                result = self.client.create(request, step=i)
            except BudgetExceeded as exc:
                self._save_partial(started, f"[予算停止] {exc}")
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            messages.append({"role": "assistant", "content": [dict(b) for b in result.content]})
            step = Step(
                i=i,
                state_hash_before=prev_hash,
                state_hash_after=prev_hash,
                assistant_text=result.text(),
                usage=result.usage,
                latency_ms=latency_ms,
                context_tokens=estimate_tokens(send, system),
            )
            tool_uses = result.tool_uses()
            effects: list[Effect] = []
            decisions: list[PolicyDecision] = []
            unexecuted: set[str] = set()
            if not tool_uses:
                # ツール無しの end_turn = 主張なしの完了（finished は False のまま）
                run.final_text = result.text()
            else:
                blocks: list[dict[str, Any]] = []  # Any: tool_result ブロック
                for block in tool_uses:
                    blocks.append(self._handle_call(i, block, step, effects, decisions, unexecuted))
                messages.append({"role": "user", "content": blocks})
            prev_hash = self._close_step(step, effects, decisions, unexecuted)
            if not tool_uses or self.finished or self.fatal:
                break

    def _handle_call(
        self,
        i: int,
        block: dict[str, Any],  # Any: tool_use ブロック
        step: Step,
        effects: list[Effect],
        decisions: list[PolicyDecision],
        unexecuted: set[str],
    ) -> dict[str, Any]:  # Any: tool_result ブロック
        """tool_use 1 個を処理して tool_result ブロックを返す（SPEC 11.2 節の手順 4）。"""
        call_args = dict(block.get("input") or {})
        call = ToolCall(
            id=block["id"],
            name=block["name"],
            args=call_args,
            args_norm=normalize_args(call_args),
        )
        step.tool_calls.append(call)
        spec = self.toolset.get(call.name)
        parsed = None
        invalid_detail = f"未知のツール: {call.name}"
        if spec is not None:
            try:
                parsed = spec.args_model.model_validate(call_args)
            except ValidationError as exc:
                invalid_detail = str(exc)[:300]
        payload: dict[str, Any]  # Any: ツール応答の JSON
        is_error = False
        out: ToolOutput | None = None
        executed = False
        if spec is None or parsed is None:
            decisions.append(
                PolicyDecision(
                    call_id=call.id,
                    capability=f"invalid:{call.name}",
                    decision="deny",
                    rule_id="harness.invalid_arguments",
                    detail=invalid_detail,
                )
            )
            payload = {"error": "invalid_arguments", "detail": invalid_detail}
            is_error = True
        else:
            denied: PolicyDecision | None = None
            if not spec.skip_broker:
                decision = self.broker.decide(call, self._capability(spec, parsed), self.task)
                decisions.append(decision)
                if decision.decision in ("deny", "ask_rejected"):
                    denied = decision
            if denied is not None:
                payload = {"error": "permission_denied", "rule_id": denied.rule_id}
                is_error = True
            elif self.fatal:
                payload = {
                    "error": "not_executed",
                    "detail": "同じステップの前の呼び出しでサンドボックスが失敗した",
                }
                is_error = True
            else:
                payload, is_error, out = self._execute(i, call, spec, parsed, effects)
                executed = not self.fatal or out is not None
        if not executed:
            unexecuted.add(call.id)
        content = json.dumps(payload, ensure_ascii=False)
        step.tool_results.append(
            ToolResult(
                id=call.id,
                content=content,
                is_error=is_error,
                bytes=len(content),
                injected=self._injection_note(i, call.name),
            )
        )
        if executed:
            raw = out.raw_chars if out is not None and out.raw_chars is not None else len(content)
            shown = (
                out.shown_chars if out is not None and out.shown_chars is not None else len(content)
            )
            self.sizes.append(
                ToolOutputSize(
                    step=i, call_id=call.id, tool=call.name, raw_chars=raw, shown_chars=shown
                )
            )
            if call.name == "finish" and not is_error and isinstance(parsed, FinishArgs):
                self.run.final_text = parsed.summary
                self.run.claims = list(parsed.claims)
                self.finished = True
            if call.name == "ask_user" and not is_error:
                self.asked_user = True
        return {
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": [{"type": "text", "text": content}],
            "is_error": is_error,
        }

    def _capability(self, spec: ToolSpec, parsed: Any) -> str:  # Any: 検証済みの引数モデル
        """ツールの能力。作業ツリー内に見えてもシンボリックリンクで外へ出る経路は outside にする。"""
        capability = spec.capability(parsed)
        parts = capability.split(":", 3)
        if len(parts) == 4 and parts[0] == "fs" and parts[2] == "repo":
            try:
                self.env.safe_path(parts[3])
            except PathEscapeError:
                return f"fs:{parts[1]}:outside:{getattr(parsed, 'path', parts[3])}"
        return capability

    def _execute(
        self,
        i: int,
        call: ToolCall,
        spec: ToolSpec,
        parsed: Any,  # Any: 検証済みの引数モデル
        effects: list[Effect],
    ) -> tuple[dict[str, Any], bool, ToolOutput | None]:  # Any: ツール応答の JSON
        """許可された呼び出しの実行。前後の目録の差を効果にする。"""
        env, fault = self.env, self.opts.fault
        before = env.scan()
        effects.extend(fs_diff_effects(self.last_scan, before, via_call_id=None))
        if fault is not None and fault.blocks(i, call.name):
            payload, is_error = fault.block(i, call.name)
            out: ToolOutput | None = None
        else:
            ctx = ToolContext(
                env=env,
                task=self.task,
                version=self.version,
                run_id=self.run_id,
                step=i,
                call_id=call.id,
                harness_env=self.harness_env,
            )
            try:
                out = spec.run(parsed, ctx)
            except SandboxError as exc:
                self.fatal = True
                after = env.scan()
                effects.extend(fs_diff_effects(before, after, via_call_id=call.id))
                self.last_scan = after
                return {"error": "sandbox_error", "detail": str(exc)[:300]}, True, None
            payload, is_error = out.payload, out.is_error
            if fault is not None:
                payload, injected = fault.apply(i, call.name, payload)
                is_error = is_error or injected
            effects.extend(out.effects)
        after = env.scan()
        effects.extend(fs_diff_effects(before, after, via_call_id=call.id))
        self.last_scan = after
        return payload, is_error, out

    def _injection_note(self, step: int, tool: str) -> dict[str, Any] | None:  # Any: 注入の記録
        records = getattr(self.opts.fault, "records", None) or []
        for record in records:
            if getattr(record, "step", None) == step and getattr(record, "tool", None) == tool:
                to_dict = getattr(record, "to_dict", None)
                note = to_dict() if callable(to_dict) else None
                return note if isinstance(note, dict) else None
        return None

    def _read_area(self, target: str) -> str | None:
        """sink_effects に渡す読み取り。repo: / home: / tmp: 以外（abs: など）とリンクは None。"""
        if target.partition(":")[0] not in ("repo", "home", "tmp"):
            return None
        try:
            return self.env.read_area_file(target)
        except (PathEscapeError, OSError):
            return None

    def _close_step(
        self,
        step: Step,
        effects: list[Effect],
        decisions: list[PolicyDecision],
        unexecuted: set[str],
    ) -> str:
        """ステップのすべての呼び出しの後の処理（SPEC 11.2 節の手順 5）。state_hash_after を返す。"""
        if step.tool_calls:
            effects.extend(
                sink_effects(
                    step.tool_calls,
                    effects,
                    read_area_file=self._read_area,
                    allowed_paths=self.task.allowed_paths,
                    scan=partial(self.vault.scan, self.run_id),
                    unexecuted_call_ids=unexecuted,
                )
            )
        allowed = {d.call_id for d in decisions if d.decision in ("allow", "ask_approved")}
        step.effects = [
            e.model_copy(update={"mediated": True}) if e.via_call_id in allowed else e
            for e in dedupe_effects(effects)
        ]
        step.policy_decisions = decisions
        step.state_hash_after = self.env.state_hash()
        ref = self.snapshotter(self.env, step.i)
        step.snapshot_ref = str(ref.path)
        self.last_ref = ref
        self.run.steps.append(step)
        return step.state_hash_after

    # --- 終了 ---------------------------------------------------------------------
    def _cost(self) -> Cost:
        """run のコスト集計。sim / replay は usd = 0。"""
        usage = Usage()
        for step in self.run.steps:
            usage.input_tokens += step.usage.input_tokens
            usage.output_tokens += step.usage.output_tokens
            usage.cache_read_input_tokens += step.usage.cache_read_input_tokens
            usage.cache_creation_input_tokens += step.usage.cache_creation_input_tokens
        usd = load_pricing().usd(_model_id(self.version), usage) if self.run_mode == "live" else 0.0
        return Cost(
            usd=round(usd, 6),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            calls=len(self.run.steps),
        )

    def _save_partial(self, started: float, note: str) -> None:
        """予算停止などの途中終了。その時点までの Run を .partial.json に残す（ADR-040）。"""
        self.run.final_text = note
        self.run.wall_time_s = round(time.monotonic() - started, 3)
        self.run.cost = self._cost()
        try:
            save_coding_run(self.run, None, partial=True, data_root=self.opts.data_root)
        except FileExistsError:
            # 前の中断の記録を上書きしない。BudgetExceeded を隠さないことを優先する
            pass

    def _finalize(self, started: float) -> tuple[Run, CodingOutcome]:
        """評価（元のテストでの可視テストと隠しテスト）と保存。合否は RepoEnv の strict な評価に任せる。"""
        run, env, opts = self.run, self.env, self.opts
        visible = env.run_visible_tests(
            timeout_s=FINAL_EVAL_TIMEOUT_S,
            original_tests=True,
            require_expected=opts.require_expected,
        )
        hidden = env.run_hidden_tests(
            timeout_s=FINAL_EVAL_TIMEOUT_S, require_expected=opts.require_expected
        )
        init_ref = CodingSnapshotRef(
            run_id=self.run_id, step=INIT_STEP, path=env.layout.snapshot_dir / "init.json"
        )
        run.outcome = Outcome(
            passed=hidden.passed,
            acceptance_results={
                ACC_FINISHED: self.finished,
                ACC_VISIBLE: visible.passed,
                ACC_HIDDEN: hidden.passed,
                ACC_ASKED_USER: self.asked_user,
            },
        )
        outcome = CodingOutcome(
            run_id=self.run_id,
            task_id=self.task.id,
            version_id=self.version.id,
            stage=opts.stage,
            repeat=opts.repeat,
            mode=self.run_mode,
            script_id=self.script_id,
            finished=self.finished,
            asked_user=self.asked_user,
            n_steps=len(run.steps),
            visible=visible,
            hidden=hidden,
            final_diff=env.diff(init_ref, None, areas=("repo",)),
            final_snapshot=str(self.last_ref.path) if self.last_ref is not None else "",
            touched_files=RepoEnv.touched_files(run),
            test_files_modified=env.test_files_modified(),
            tool_output_sizes=self.sizes,
        )
        run.injections = [*run.injections, *self._fault_records()]
        run.wall_time_s = round(time.monotonic() - started, 3)
        run.cost = self._cost()
        save_coding_run(run, outcome, data_root=opts.data_root)
        return run, outcome

    def _fault_records(self) -> list[dict[str, Any]]:  # Any: 注入の記録
        out: list[dict[str, Any]] = []
        for record in getattr(self.opts.fault, "records", None) or []:
            to_dict = getattr(record, "to_dict", None)
            if callable(to_dict):
                item = to_dict()
                if isinstance(item, dict):
                    out.append(item)
        return out


def _default_snapshot(env: RepoEnv, step: int) -> CodingSnapshotRef:
    return env.snapshot(step)


def _model_id(version: CodingVersion) -> str:
    from agenteval.llm.models import model_id

    return model_id(version.model)
