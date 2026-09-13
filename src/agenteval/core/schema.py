"""軌跡・スナップショット・版ハッシュのスキーマ（docs/plan/01-architecture.md）。

このファイルを変えるときは移行スクリプトと ADR を同時に書く（CLAUDE.md）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Provenance = Literal["live", "replay", "simulated"]
RunMode = Literal["live", "replay", "branch", "sim"]


class ToolCall(BaseModel):
    """1 回のツール呼び出し。`args_norm` は normalize_args の結果。"""

    id: str
    name: str
    args: dict[str, Any]
    args_norm: dict[str, Any]


class ToolResult(BaseModel):
    """ツール実行結果。`injected` は注入器が介入した場合のみ非 None。"""

    id: str
    content: str
    is_error: bool = False
    bytes: int = 0
    injected: dict[str, Any] | None = None


class Usage(BaseModel):
    """1 呼び出しのトークン使用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class Step(BaseModel):
    """1 ステップ = 1 回の messages.create ＋ そのツール実行。"""

    i: int
    state_hash_before: str
    state_hash_after: str
    assistant_text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    context_tokens: int = 0
    snapshot_ref: str = ""


class Violation(BaseModel):
    """リンター違反 1 件。"""

    rule_id: str
    step: int
    detail: str


class Outcome(BaseModel):
    """run の結果。`passed` と linter 違反は独立（原典 4.1 の右上象限）。"""

    passed: bool
    acceptance_results: dict[str, bool] = Field(default_factory=dict)
    milestone_score: float = 0.0
    milestone_steps: dict[str, int] = Field(default_factory=dict)
    linter_violations: list[Violation] = Field(default_factory=list)


class Cost(BaseModel):
    """run 単位のコスト集計。replay / sim では usd = 0。"""

    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


class Run(BaseModel):
    """1 タスク 1 版 1 回の実行。すべての章の実装はこれだけを入力にする。"""

    run_id: str
    task_id: str
    version_id: str
    version_hash: str
    mode: RunMode
    seed: int
    injections: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    final_text: str = ""
    claims: list[str] = Field(default_factory=list)
    plan: list[dict[str, Any]] | None = None
    outcome: Outcome | None = None
    cost: Cost = Field(default_factory=Cost)
    wall_time_s: float = 0.0
    repeat: int = 0
    parent_run_id: str | None = None
    divergence_step: int | None = None
    provenance: Provenance = "simulated"

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    def tool_names(self) -> list[str]:
        """実行順のツール名列（δ の計算や同値判定に使う）。"""
        return [c.name for s in self.steps for c in s.tool_calls]

    def passed(self) -> bool:
        return bool(self.outcome and self.outcome.passed)


class Change(BaseModel):
    """版と版の差分（3.1）。`components` は kind ごとの粒度の集合。"""

    kind: Literal["prompt", "model", "tool", "config", "fixture", "code"]
    base: str
    target: str
    components: set[str] = Field(default_factory=set)

    def label(self) -> str:
        return f"{self.base}->{self.target}({self.kind})"
