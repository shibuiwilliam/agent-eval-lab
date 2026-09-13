"""LLM クライアント・カセット・コストの単体テスト（API を呼ばない）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.core.schema import Usage
from agenteval.llm import cassette
from agenteval.llm.client import LLMClient, MessageRequest, MessageResult
from agenteval.llm.cost import (
    BudgetExceeded,
    BudgetGuard,
    LedgerEntry,
    append_ledger,
    ledger_summary,
    load_pricing,
)


def _req() -> MessageRequest:
    return MessageRequest(
        model="claude-haiku-4-5-20251001", system="s", messages=[{"role": "user", "content": "hi"}]
    )


def test_cassette_key_ignores_nothing_but_is_stable() -> None:
    a = cassette.cassette_key(_req().cassette_body())
    b = cassette.cassette_key(_req().cassette_body())
    assert a == b and len(a) == 64


def test_cassette_roundtrip(tmp_path: Path) -> None:
    req = _req()
    key = cassette.cassette_key(req.cassette_body())
    cassette.save(
        req.model,
        key,
        req.cassette_body(),
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
        {"input_tokens": 10, "output_tokens": 2},
        "test",
        root=tmp_path,
    )
    data = cassette.load(req.model, key, roots=[tmp_path])
    assert data["response"]["content"][0]["text"] == "ok"


def test_replay_miss_raises(tmp_path: Path) -> None:
    with pytest.raises(cassette.CassetteMiss):
        cassette.load("m", "0" * 64, roots=[tmp_path])


def test_replay_mode_uses_cassette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    req = _req()
    key = cassette.cassette_key(req.cassette_body())
    cassette.save(
        req.model,
        key,
        req.cassette_body(),
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
        {"input_tokens": 10, "output_tokens": 2},
        "test",
        root=tmp_path,
    )
    monkeypatch.setattr(cassette, "CASSETTE_ROOT", tmp_path)
    client = LLMClient(mode="replay", ledger_path=tmp_path / "ledger.jsonl")
    result = client.create(req)
    assert result.source == "replay"
    assert result.text() == "ok"
    assert ledger_summary(tmp_path / "ledger.jsonl")["usd"] == 0.0


def test_live_without_key_is_blocked(tmp_path: Path) -> None:
    client = LLMClient(mode="record", ledger_path=tmp_path / "l.jsonl")
    with pytest.raises(BudgetExceeded):
        client.create(_req())


def test_budget_guard_stops_before_call(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(
        LedgerEntry(
            ts="t",
            run_id="r",
            step=0,
            model="m",
            mode="live",
            usd=9.99,
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        ledger,
    )
    guard = BudgetGuard(budget_usd=10.0, ledger_path=ledger)
    guard.record(0.05)
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_pricing_is_read_from_yaml() -> None:
    pricing = load_pricing()
    usd = pricing.usd("claude-haiku-4-5-20251001", Usage(input_tokens=1_000_000))
    assert usd == pricing.models["claude-haiku-4-5-20251001"]["input"]


def test_message_result_helpers() -> None:
    result = MessageResult(
        content=[
            {"type": "text", "text": "a"},
            {"type": "tool_use", "id": "1", "name": "finish", "input": {}},
        ],
        stop_reason="tool_use",
    )
    assert result.text() == "a"
    assert len(result.tool_uses()) == 1
