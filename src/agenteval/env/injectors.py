"""注入器。注入は必ず run manifest に記録する（trace から見えない注入を作らない）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from agenteval.env.external import ExternalService
from agenteval.env.office import OfficeEnv

FaultKind = Literal["timeout", "error", "partial", "schema_change"]


@dataclass
class Injection:
    """注入 1 件の記録。"""

    kind: str
    step: int
    tool: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "step": self.step, "tool": self.tool, "detail": self.detail}


@dataclass
class FaultInjector:
    """指定ツール・指定ステップに障害を起こす。"""

    tool: str
    at_steps: tuple[int, ...]
    fault: FaultKind = "error"
    records: list[Injection] = field(default_factory=list)

    def blocks(self, step: int, tool: str) -> bool:
        """このステップでツールの**実行そのもの**を止めるか（ADR-026）。

        `timeout` / `error` は「呼び出しが失敗した」障害なので、環境に副作用を残してはいけない。
        `partial` / `schema_change` は成功した応答を加工する障害なので、実行してから適用する。
        """
        return tool == self.tool and step in self.at_steps and self.fault in ("timeout", "error")

    def block(self, step: int, tool: str) -> tuple[dict[str, Any], bool]:
        """ツールを実行せずにエラーを返す。"""
        self.records.append(Injection(kind=f"fault:{self.fault}", step=step, tool=tool))
        if self.fault == "timeout":
            return {"error": "timeout", "detail": "サービスが応答しませんでした"}, True
        return {"error": "internal_error", "detail": "一時的な障害"}, True

    def apply(self, step: int, tool: str, result: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """成功した応答を加工する障害（`partial` / `schema_change`）を適用する。

        `timeout` / `error` は `blocks()` 側で扱うので、ここでは何もしない。
        """
        if tool != self.tool or step not in self.at_steps:
            return result, False
        if self.fault in ("timeout", "error"):
            return result, False
        self.records.append(Injection(kind=f"fault:{self.fault}", step=step, tool=tool))
        if self.fault == "partial":
            truncated = {k: v for i, (k, v) in enumerate(result.items()) if i == 0}
            truncated["truncated"] = True
            return truncated, False
        renamed = {f"{k}_v2": v for k, v in result.items()}
        return renamed, False


@dataclass
class NoiseInjector:
    """ステップ k 以降 n ステップのツール応答に無関係な長文を付加する（4.9）。"""

    start_step: int
    n_steps: int
    seed: int = 0
    chars: int = 1200
    records: list[Injection] = field(default_factory=list)

    def apply(self, step: int, tool: str, result: dict[str, Any]) -> dict[str, Any]:
        if not (self.start_step <= step < self.start_step + self.n_steps):
            return result
        rng = np.random.default_rng(self.seed + step)
        topics = ["社内報", "福利厚生", "システム保守", "研修案内", "設備点検", "アンケート"]
        pieces: list[str] = []
        while sum(len(piece) for piece in pieces) < self.chars:
            topic = topics[int(rng.integers(0, len(topics)))]
            num = int(rng.integers(100, 999))
            pieces.append(f"【{topic}のお知らせ {num}】本件は今回の依頼とは関係ありません。")
        noise = "".join(pieces)[: self.chars]
        self.records.append(
            Injection(kind="noise", step=step, tool=tool, detail={"chars": len(noise)})
        )
        out = dict(result)
        out["_notice"] = noise
        return out


@dataclass
class DriftInjector:
    """外部サービスの版を上げる／フィクスチャを古くする（8章）。"""

    external_version: str | None = None
    records: list[Injection] = field(default_factory=list)

    def bump_external(self, env: OfficeEnv, version: str = "v2") -> None:
        """`ExternalService` の版を上げる。"""
        env.external = ExternalService(version=version)  # type: ignore[arg-type]
        self.external_version = version
        self.records.append(
            Injection(
                kind="drift:external", step=-1, tool="external_lookup", detail={"version": version}
            )
        )
