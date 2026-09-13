"""LLM 呼び出しの唯一の入口。記録／再生／コスト計測／予算停止をここに集約する。

モード（環境変数 `AGENTEVAL_LLM_MODE`）:
  - `replay`   : カセットのみ。miss は `CassetteMiss`
  - `record`   : 常に live、カセットを上書き
  - `auto`     : hit なら replay、miss なら live ＋ 保存
  - `sim`      : 決定的シミュレータ（ADR-008）。API を呼ばない。来歴は `simulated`
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agenteval.core.schema import Usage
from agenteval.llm import cassette
from agenteval.llm.cost import (
    BudgetExceeded,
    BudgetGuard,
    LedgerEntry,
    append_ledger,
    load_pricing,
    now_iso,
)

Mode = Literal["replay", "record", "auto", "sim"]


class MessageRequest(BaseModel):
    """`messages.create` のリクエスト。サンプリング引数は持たない（ADR-002）。"""

    model: str
    system: list[dict[str, Any]] | str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: dict[str, Any] | None = None
    max_tokens: int = 1024
    # `output_config.effort` 用。対応しないモデルに渡すと 400 になるので、
    # 呼び出し側が `models.supports_effort()` で確認してから設定する
    output_config: dict[str, Any] | None = None

    def cassette_body(self) -> dict[str, Any]:
        """ハッシュ対象の本文。"""
        return self.model_dump(exclude_none=True)


class MessageResult(BaseModel):
    """`messages.create` の応答（必要な部分だけ）。"""

    content: list[dict[str, Any]]
    stop_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    source: Literal["live", "replay", "sim"] = "sim"

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


def current_mode() -> Mode:
    """環境変数からモードを読む。既定は sim（API キーを要求しない）。"""
    raw = os.environ.get("AGENTEVAL_LLM_MODE", "sim").lower()
    if raw not in ("replay", "record", "auto", "sim"):
        raise ValueError(f"未知の AGENTEVAL_LLM_MODE: {raw}")
    return raw  # type: ignore[return-value]


class LLMClient:
    """すべての LLM 呼び出しが通る唯一のクライアント。"""

    def __init__(
        self,
        mode: Mode | None = None,
        run_id: str = "-",
        budget: BudgetGuard | None = None,
        ledger_path: Path | None = None,
        simulator: Any | None = None,
    ) -> None:
        self.mode: Mode = mode or current_mode()
        self.run_id = run_id
        self.budget = budget or BudgetGuard(ledger_path=ledger_path)
        self.ledger_path = ledger_path
        self.simulator = simulator
        self._client: Any | None = None
        self.calls = 0

    # --- 公開 API ---------------------------------------------------------
    def create(self, req: MessageRequest, step: int = -1) -> MessageResult:
        """1 呼び出し。モードに応じて replay / live / sim を選ぶ。"""
        self.calls += 1
        key = cassette.cassette_key(req.cassette_body())
        if self.mode == "sim":
            return self._simulate(req, step)
        if self.mode in ("replay", "auto"):
            try:
                data = cassette.load(req.model, key)
            except cassette.CassetteMiss:
                if self.mode == "replay":
                    raise
            else:
                return self._from_cassette(data, step, req.model)
        return self._live(req, key, step)

    # --- 内部 -------------------------------------------------------------
    def _simulate(self, req: MessageRequest, step: int) -> MessageResult:
        if self.simulator is None:
            raise RuntimeError("sim モードだが simulator が渡されていない")
        result: MessageResult = self.simulator.respond(req)
        self._ledger(req.model, "sim", 0.0, result.usage, step)
        return result

    def _from_cassette(self, data: dict[str, Any], step: int, model: str) -> MessageResult:
        response = data["response"]
        usage = Usage.model_validate(data.get("usage", {}))
        self._ledger(model, "replay", 0.0, usage, step)
        return MessageResult(
            content=response.get("content", []),
            stop_reason=response.get("stop_reason"),
            usage=usage,
            source="replay",
        )

    def _live(self, req: MessageRequest, key: str, step: int) -> MessageResult:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise BudgetExceeded("ANTHROPIC_API_KEY が無いため live 呼び出しを行いません")
        self.budget.check()
        client = self._ensure_client()
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": req.model,
            "system": req.system,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
        }
        if req.tools:
            kwargs["tools"] = req.tools
        if req.tool_choice:
            kwargs["tool_choice"] = req.tool_choice
        if req.output_config:
            kwargs["output_config"] = req.output_config
        message = client.messages.create(**kwargs)
        elapsed = time.monotonic() - started
        dumped: dict[str, Any] = message.model_dump()
        usage = Usage.model_validate(
            {k: v for k, v in (dumped.get("usage") or {}).items() if isinstance(v, int)}
        )
        usd = load_pricing().usd(req.model, usage)
        self.budget.record(usd)
        cassette.save(
            req.model,
            key,
            req.cassette_body(),
            dumped,
            usage.model_dump(),
            sdk_version=_sdk_version(),
        )
        self._ledger(req.model, "live", usd, usage, step, latency_s=elapsed)
        return MessageResult(
            content=[b for b in dumped.get("content", [])],
            stop_reason=dumped.get("stop_reason"),
            usage=usage,
            source="live",
        )

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(max_retries=4, timeout=120)
        return self._client

    def _ledger(
        self, model: str, mode: str, usd: float, usage: Usage, step: int, latency_s: float = 0.0
    ) -> None:
        append_ledger(
            LedgerEntry(
                ts=now_iso(),
                run_id=self.run_id,
                step=step,
                model=model,
                mode=mode,
                usd=usd,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
            ),
            self.ledger_path,
        )


def _sdk_version() -> str:
    try:
        import anthropic

        return str(getattr(anthropic, "__version__", "unknown"))
    except ImportError:  # pragma: no cover - SDK 未導入環境
        return "unknown"
