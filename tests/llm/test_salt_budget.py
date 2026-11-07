"""カセットの塩（ADR-038）と台帳の総額キャッシュ・実験単位の予算（ADR-040）のテスト。API は呼ばない。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agenteval.core.normalize import sha256_of
from agenteval.llm import cassette, cost
from agenteval.llm.client import LLMClient, MessageRequest
from agenteval.llm.cost import (
    BudgetExceeded,
    BudgetGuard,
    LedgerEntry,
    LedgerTotalCache,
    append_ledger,
    experiment_budget,
    ledger_total_usd,
    read_ledger,
)

MODEL = "claude-haiku-4-5-20251001"


def _req(text: str = "hi") -> MessageRequest:
    return MessageRequest(model=MODEL, system="s", messages=[{"role": "user", "content": text}])


def _save(req: MessageRequest, key: str, root: Path, text: str, salt: str | None = None) -> Path:
    return cassette.save(
        req.model,
        key,
        req.cassette_body(),
        {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"},
        {"input_tokens": 1, "output_tokens": 1},
        "test",
        root=root,
        salt=salt,
    )


def _entry(usd: float) -> LedgerEntry:
    return LedgerEntry(
        ts="t",
        run_id="r",
        step=0,
        model="m",
        mode="live",
        usd=usd,
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


# --- 塩: 塩なしは従来と同一 --------------------------------------------------
def test_unsalted_key_is_identical_to_legacy_sha256() -> None:
    body = _req().cassette_body()
    assert cassette.cassette_key(body) == sha256_of(body)
    assert cassette.cassette_key(body, None) == sha256_of(body)
    # 実装が変わっても既存カセットが当たり続けることを固定値で示す（従来実装の出力）
    legacy = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert (
        cassette.cassette_key(legacy)
        == "6d139f1403cd84518768857efed6c375c0e3fe1af3f250cd3a5c59ea87a0323c"
    )


def test_unsalted_payload_is_identical_to_legacy(tmp_path: Path) -> None:
    req = _req()
    path = _save(req, cassette.cassette_key(req.cassette_body()), tmp_path, "ok")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"request", "response", "usage", "recorded_at", "sdk_version"}
    assert "salt" not in payload


def test_legacy_cassette_still_hits_in_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """塩なしの client が、従来の鍵・従来の配置で置かれたカセットを再生する。"""
    req = _req()
    legacy_key = sha256_of(req.cassette_body())
    _save(req, legacy_key, tmp_path, "legacy")
    monkeypatch.setattr(cassette, "CASSETTE_ROOT", tmp_path)
    client = LLMClient(mode="replay", ledger_path=tmp_path / "l.jsonl")
    assert client.create(req).text() == "legacy"


# --- 塩: 塩ありは別鍵 ----------------------------------------------------------
def test_salted_keys_differ_by_salt_and_from_unsalted() -> None:
    body = _req().cassette_body()
    keys = {
        cassette.cassette_key(body),
        cassette.cassette_key(body, "cd:T:v:s1:r0"),
        cassette.cassette_key(body, "cd:T:v:s1:r1"),
    }
    assert len(keys) == 3
    assert cassette.cassette_key(body, "x") == sha256_of(
        {cassette.SALT_FIELD: "x", "request": body}
    )
    assert cassette.cassette_key(body, "x") == cassette.cassette_key(body, "x")


def test_empty_salt_is_value_error(tmp_path: Path) -> None:
    body = _req().cassette_body()
    with pytest.raises(ValueError):
        cassette.cassette_key(body, "")
    with pytest.raises(ValueError):
        cassette.save(MODEL, "0" * 64, body, {}, {}, "t", root=tmp_path, salt="")
    with pytest.raises(ValueError):
        LLMClient(mode="replay", cassette_salt="", ledger_path=tmp_path / "l.jsonl")


def test_salted_payload_records_salt_and_keeps_request(tmp_path: Path) -> None:
    req = _req()
    key = cassette.cassette_key(req.cassette_body(), "s1")
    path = _save(req, key, tmp_path, "ok", salt="s1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["salt"] == "s1"
    assert payload["request"] == req.cassette_body()  # 塩は request に混ぜない


def test_repeats_with_different_salt_do_not_collapse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一リクエストでも、反復ごとの塩で別カセットになり、各反復が自分の記録を再生する。"""
    req = _req()
    for rep in range(3):
        salt = f"cd:T-1:v:s1:r{rep}"
        _save(req, cassette.cassette_key(req.cassette_body(), salt), tmp_path, f"rep{rep}", salt)
    assert len(list(tmp_path.rglob("*.json"))) == 3
    monkeypatch.setattr(cassette, "CASSETTE_ROOT", tmp_path)
    for rep in range(3):
        client = LLMClient(
            mode="replay",
            ledger_path=tmp_path / "l.jsonl",
            cassette_salt=f"cd:T-1:v:s1:r{rep}",
        )
        assert client.create(req).text() == f"rep{rep}"
    # 塩の無い client は、塩つきカセットに当たらない
    with pytest.raises(cassette.CassetteMiss):
        LLMClient(mode="replay", ledger_path=tmp_path / "l.jsonl").create(req)


def test_live_saves_with_salt_and_sends_no_salt_to_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_live は塩を保存にだけ使い、API の kwargs には入れない。"""
    sent: dict[str, Any] = {}

    class _Msg:
        def model_dump(self) -> dict[str, Any]:
            return {
                "content": [{"type": "text", "text": "live"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    class _Messages:
        def create(self, **kwargs: Any) -> _Msg:
            sent.update(kwargs)
            return _Msg()

    class _Api:
        messages = _Messages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setattr(cassette, "CASSETTE_ROOT", tmp_path / "c")
    # save() の既定 root は CASSETTE_ROOT を関数内で読むので、差し替えで tmp_path に向く
    ledger = tmp_path / "l.jsonl"
    client = LLMClient(
        mode="record",
        ledger_path=ledger,
        cassette_salt="salt-A",
        budget=BudgetGuard(budget_usd=100.0, ledger_path=ledger),
    )
    client._client = _Api()
    req = _req()
    assert client.create(req).source == "live"
    assert "salt-A" not in json.dumps(sent, default=str) and "salt" not in sent
    key = cassette.cassette_key(req.cassette_body(), "salt-A")
    data = cassette.load(MODEL, key, roots=[tmp_path / "c"])
    assert data["salt"] == "salt-A"


# --- 台帳の総額キャッシュ -----------------------------------------------------
def test_total_cache_matches_full_read_and_is_incremental(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    assert ledger_total_usd(ledger) == 0.0
    for usd in (0.1, 0.2, 0.3):
        append_ledger(_entry(usd), ledger)
    # Python 3.12 以降の sum() は補償加算なので、素朴な逐次加算とは 1e-16 程度ずれる。許容差は SPEC どおり 1e-9
    assert abs(ledger_total_usd(ledger) - sum(e.usd for e in read_ledger(ledger))) <= 1e-9
    cache = LedgerTotalCache.model_validate_json(
        (tmp_path / "l.jsonl.total.json").read_text(encoding="utf-8")
    )
    assert cache.offset == ledger.stat().st_size and cache.lines == 3
    append_ledger(_entry(0.4), ledger)
    assert abs(ledger_total_usd(ledger) - sum(e.usd for e in read_ledger(ledger))) <= 1e-9
    assert abs(ledger_total_usd(ledger, use_cache=False) - ledger_total_usd(ledger)) <= 1e-9


def test_total_cache_uses_default_ledger_path_at_call_time() -> None:
    # conftest が cost.LEDGER_PATH を tmp_path に差し替えている。既定値に束縛していれば data/ を読んでしまう
    append_ledger(_entry(1.5))
    assert ledger_total_usd() == 1.5
    assert BudgetGuard(budget_usd=10.0).spent == 1.5


def test_total_cache_ignores_partial_last_line(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(_entry(0.5), ledger)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(_entry(9.0).model_dump_json()[:20])  # 書き込み途中（改行なし）
    assert ledger_total_usd(ledger) == pytest.approx(0.5)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(_entry(9.0).model_dump_json()[20:] + "\n")  # 完了した行は次回から入る
    assert ledger_total_usd(ledger) == pytest.approx(9.5)


def test_total_cache_rebuilds_when_ledger_rewritten_or_shrunk(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    for usd in (1.0, 2.0):
        append_ledger(_entry(usd), ledger)
    assert ledger_total_usd(ledger) == pytest.approx(3.0)
    ledger.write_text(_entry(0.25).model_dump_json() + "\n", encoding="utf-8")  # 縮めた
    assert ledger_total_usd(ledger) == pytest.approx(0.25)
    # 先頭が違う別内容に置き換え（サイズは増える）
    ledger.write_text("".join(_entry(u).model_dump_json() + "\n" for u in (7.0, 8.0, 9.0)))
    assert ledger_total_usd(ledger) == pytest.approx(24.0)


def test_total_cache_corrupt_cache_falls_back_to_full_read(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(_entry(2.0), ledger)
    ledger_total_usd(ledger)
    (tmp_path / "l.jsonl.total.json").write_text("{not json", encoding="utf-8")
    assert ledger_total_usd(ledger) == pytest.approx(2.0)


def test_total_invalid_line_is_value_error(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ledger_total_usd(ledger)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    batches=st.lists(
        st.lists(st.floats(min_value=0.0, max_value=50.0, allow_nan=False), max_size=6),
        min_size=1,
        max_size=6,
    )
)
def test_property_cached_total_equals_full_read(
    tmp_path_factory: pytest.TempPathFactory, batches: list[list[float]]
) -> None:
    """追記とキャッシュ更新を任意に交互に行っても、総額は全件読みと一致する。"""
    ledger = tmp_path_factory.mktemp("ledger") / "l.jsonl"
    for batch in batches:
        for usd in batch:
            append_ledger(_entry(usd), ledger)
        total = ledger_total_usd(ledger)
        assert abs(total - sum(e.usd for e in read_ledger(ledger))) <= 1e-9
    assert abs(ledger_total_usd(ledger, use_cache=False) - ledger_total_usd(ledger)) <= 1e-9


# --- 実験単位の予算 -----------------------------------------------------------
def test_experiment_budget_caps_at_spent_plus_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(_entry(3.0), ledger)
    monkeypatch.setenv("AGENTEVAL_BUDGET_USD", "100")
    guard = experiment_budget({"budget_usd": 0.5}, ledger_path=ledger)
    assert guard.budget == pytest.approx(3.5) and guard.spent == pytest.approx(3.0)
    guard.check()  # 3.0 + 0.01 <= 3.5
    guard.record(0.495)
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_experiment_budget_zero_stops_before_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(_entry(1.0), ledger)
    monkeypatch.setenv("AGENTEVAL_BUDGET_USD", "100")
    for entry in ({"budget_usd": 0}, {}):
        guard = experiment_budget(entry, ledger_path=ledger)
        assert guard.budget == guard.spent
        with pytest.raises(BudgetExceeded):
            guard.check()


def test_experiment_budget_is_capped_by_cumulative_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "l.jsonl"
    append_ledger(_entry(9.0), ledger)
    monkeypatch.setenv("AGENTEVAL_BUDGET_USD", "10")
    assert experiment_budget({"budget_usd": 5}, ledger_path=ledger).budget == 10.0


def test_experiment_budget_without_env_keeps_unset_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTEVAL_BUDGET_USD", raising=False)
    guard = experiment_budget({"budget_usd": 5}, ledger_path=tmp_path / "l.jsonl")
    assert guard.budget is None
    with pytest.raises(BudgetExceeded, match="未設定"):
        guard.check()


def test_experiment_budget_rejects_negative(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        experiment_budget({"budget_usd": -1}, ledger_path=tmp_path / "l.jsonl")


def test_module_exports_cost_names() -> None:
    assert cost.ledger_total_usd is ledger_total_usd
