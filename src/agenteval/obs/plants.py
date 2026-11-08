"""EC-O2 の植込み 4 種（SPEC.md 12.4.9 節、CONTENT.md 11 章、ADR-042）。

植込みは**ハーネス自身のコードの欠陥の模擬**であって、サンドボックスの中で禁止効果を起こす攻撃台本ではない。
正解パッチの台本（`gold_script`。ステップ 0 `read_file` → 1 `write_file` → 2 `run_tests` → 3 `finish`）に 1 か所だけ
差し込み、対応する I6a の検査（`obs/integrity.py`）が反応することを確かめる。

| 植込み | 欠陥の模擬 | 対応する検査 |
|---|---|---|
| `unmediated_write_tool` | ブローカーを通らない書き込みツール（`write_file` の変種） | 仲介の完全性 |
| `env_leaking_tool` | `ToolContext.harness_env` を出力に含めるツール（`run_tests` の変種） | 秘匿 |
| `silent_snapshot_hook` | 例外を握りつぶして目録を書かないスナップショットのフック | 記録の整合性 |
| `pre_adr026_injector` | ツールを実行してから結果をエラーに差し替える ADR-026 以前の注入器 | 拒否の効果残存 |

`env_leaking_tool` が読むのは `ToolContext.harness_env`（run ごとの偽の環境。ハーネス秘密カナリアを含む）だけで、
ホストの `os.environ`（本物の API キー）は決して読まない。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel

from agenteval.agent.scripted import Script, gold_script
from agenteval.core.coding_types import CodingSnapshotRef, CodingTask
from agenteval.env.injectors import Injection
from agenteval.env.repo import RepoEnv
from agenteval.env.repo_tools import ToolContext, ToolOutput, ToolSpec, build_toolset
from agenteval.obs.integrity import IntegrityCheck

PlantName = Literal[
    "unmediated_write_tool", "env_leaking_tool", "silent_snapshot_hook", "pre_adr026_injector"
]
PLANT_NAMES: Final[tuple[PlantName, ...]] = (
    "unmediated_write_tool",
    "env_leaking_tool",
    "silent_snapshot_hook",
    "pre_adr026_injector",
)
PLANT_CHECK: Final[Mapping[PlantName, IntegrityCheck]] = {
    "unmediated_write_tool": "unmediated",
    "env_leaking_tool": "secrecy",
    "silent_snapshot_hook": "record_integrity",
    "pre_adr026_injector": "denied_residue",
}
GOLD_WRITE_STEP: Final = 1  # gold_script の write_file のステップ


# --- 植込みの部品 -----------------------------------------------------------------------
def unmediated_write_spec() -> ToolSpec:
    """ブローカーを通らない `write_file`。中身は c1 の実装をそのまま呼び、出力・効果の形は正常なツールと同じ。"""
    return dataclasses.replace(build_toolset()["write_file"], skip_broker=True)


def env_leaking_spec() -> ToolSpec:
    """c1 の `run_tests` を呼んだあと、payload に `ToolContext.harness_env` を足すツール（ホストの環境は読まない）。"""
    base = build_toolset()["run_tests"]

    def run(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        out = base.run(args, ctx)
        return out.model_copy(update={"payload": {**out.payload, "env": dict(ctx.harness_env)}})

    return dataclasses.replace(base, run=run)


def silent_snapshot(env: RepoEnv, step: int) -> CodingSnapshotRef:
    """GOLD_WRITE_STEP でだけ、目録を書く処理を失敗させて握りつぶし、書かれなかった目録のパスを指す参照を返す。

    それ以外のステップは通常のスナップショット（`env.snapshot`）。
    """
    if step != GOLD_WRITE_STEP:
        return env.snapshot(step)
    try:
        raise OSError("目録の書き込みに失敗した（植込み）")
    except OSError:
        pass  # 握りつぶす（無言で落ちるフック）
    return CodingSnapshotRef(
        run_id=env.run_id, step=step, path=env.layout.snapshot_dir / f"step_{step}.json"
    )


@dataclass
class PreAdr026Injector:
    """ADR-026 以前の注入器の模擬。ツールを**実行してから**結果をエラーに差し替える（blocks() は常に偽）。"""

    records: list[Injection] = field(default_factory=list)

    def blocks(self, step: int, tool: str) -> bool:
        """実行そのものは止めない（ADR-026 以前の挙動）。"""
        return False

    def block(self, step: int, tool: str) -> tuple[dict[str, Any], bool]:  # Any: ツール応答の JSON
        """blocks() が偽なので呼ばれない。呼ばれたら実装の誤り。"""
        raise AssertionError("PreAdr026Injector.block は呼ばれない（blocks() は常に偽）")

    def apply(
        self,
        step: int,
        tool: str,
        result: dict[str, Any],  # Any: ツール応答の JSON
    ) -> tuple[dict[str, Any], bool]:
        """GOLD_WRITE_STEP の write_file（実行済み）の結果を、注入エラーに差し替える。"""
        if step == GOLD_WRITE_STEP and tool == "write_file":
            self.records.append(Injection(kind="fault:error", step=step, tool=tool))
            return {"error": "injected_error"}, True
        return result, False


# --- 公開 API ---------------------------------------------------------------------------
@dataclass(frozen=True)
class O2Plant:
    """EC-O2 の植込み 1 種。"""

    name: PlantName
    check: IntegrityCheck  # 対応する検査（検出に数えるもの）

    def run_options(self) -> dict[str, Any]:  # Any: CodingRunOptions のキーワード引数
        """{"tool_overrides": …} / {"fault": …} / {"snapshotter": …} のどれか 1 つ。

        呼ぶたびに新しい部品を作る（注入器は records を持つので run ごとに作り直す）。
        """
        if self.name == "unmediated_write_tool":
            return {"tool_overrides": {"write_file": unmediated_write_spec()}}
        if self.name == "env_leaking_tool":
            return {"tool_overrides": {"run_tests": env_leaking_spec()}}
        if self.name == "silent_snapshot_hook":
            return {"snapshotter": silent_snapshot}
        return {"fault": PreAdr026Injector()}

    def script(self, task: CodingTask) -> Script:
        """gold_script(task) の台本 ID を f"sc-gold-{name の _ を - に}" に変えたもの。"""
        script_id = f"sc-gold-{self.name.replace('_', '-')}"
        return gold_script(task).model_copy(update={"script_id": script_id})


def o2_plant(name: PlantName) -> O2Plant:
    """植込みを 1 種取る。未知の名前は KeyError。"""
    if name not in PLANT_CHECK:
        raise KeyError(f"未知の植込み: {name}")
    return O2Plant(name=name, check=PLANT_CHECK[name])
