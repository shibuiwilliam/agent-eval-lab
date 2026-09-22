"""注入器の単体テスト（ADR-026 の回帰テストを含む）。API を呼ばない。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.env.fixtures import load_sql
from agenteval.env.injectors import FaultInjector, NoiseInjector
from agenteval.env.office import OfficeEnv


@pytest.fixture
def env(tmp_path: Path) -> OfficeEnv:
    return OfficeEnv.create(tmp_path / "e.sqlite", load_sql("office_small_v1"), run_id="t")


@pytest.mark.parametrize("fault", ["error", "timeout"])
def test_failing_faults_block_execution(fault: str) -> None:
    """ADR-026: `error` / `timeout` は**実行を止める**種類の障害である。"""
    injector = FaultInjector(tool="calendar_create", at_steps=(0, 1), fault=fault)  # type: ignore[arg-type]
    assert injector.blocks(0, "calendar_create")
    assert not injector.blocks(0, "mail_send"), "別のツールは止めない"
    assert not injector.blocks(9, "calendar_create"), "対象外のステップは止めない"
    payload, is_error = injector.block(0, "calendar_create")
    assert is_error and "error" in payload
    assert injector.records and injector.records[0].tool == "calendar_create"


@pytest.mark.parametrize("fault", ["partial", "schema_change"])
def test_response_faults_do_not_block_execution(fault: str) -> None:
    """応答を加工する障害は実行を止めない（成功した応答が要る）。"""
    injector = FaultInjector(tool="external_lookup", at_steps=(0,), fault=fault)  # type: ignore[arg-type]
    assert not injector.blocks(0, "external_lookup")
    out, is_error = injector.apply(0, "external_lookup", {"price": 100, "unit": "JPY"})
    assert not is_error
    assert out != {"price": 100, "unit": "JPY"}


def test_apply_is_a_noop_for_blocking_faults() -> None:
    """`error` を `apply` に通しても応答を書き換えない（二重適用を防ぐ）。"""
    injector = FaultInjector(tool="file_write", at_steps=(0,), fault="error")
    original = {"written": True, "path": "docs/a.md"}
    assert injector.apply(0, "file_write", original) == (original, False)
    assert injector.records == [], "apply 側で記録を二重に積まない"


def test_blocked_write_leaves_no_side_effect(env: OfficeEnv) -> None:
    """**この実験の核心**: 失敗した書き込みは環境に副作用を残さない。

    改訂前はツールを実行してから応答だけをエラーに差し替えていたため、
    予定は作成されているのにエージェントには「失敗」と見え、受入基準は通ってしまった。
    その結果、書き込み系ツールに障害を入れた合成変更が 1 件も回帰を生まなかった。
    """
    from agenteval.agent.loop import RunOptions, _execute_tool
    from agenteval.core.registry import get_version

    version = get_version("v01_baseline")
    args = {
        "title": "障害テスト",
        "start": "2026-09-24T10:00",
        "end": "2026-09-24T10:30",
        "room": "A",
    }
    before = env.state_hash()
    options = RunOptions(fault=FaultInjector(tool="calendar_create", at_steps=(0,), fault="error"))
    payload, is_error = _execute_tool(env, "calendar_create", args, version, options, 0)
    assert is_error and "error" in payload
    assert env.state_hash() == before, "失敗したはずの書き込みが環境に残っている"

    # 障害が無ければ同じ呼び出しは状態を変える（テストが空振りしていないことの確認）
    _payload2, is_error2 = _execute_tool(env, "calendar_create", args, version, RunOptions(), 0)
    assert not is_error2
    assert env.state_hash() != before


def test_noise_injector_only_touches_the_window() -> None:
    injector = NoiseInjector(start_step=1, n_steps=2, seed=0)
    assert "_notice" not in injector.apply(0, "mail_search", {"x": 1})
    assert "_notice" in injector.apply(1, "mail_search", {"x": 1})
    assert "_notice" in injector.apply(2, "mail_search", {"x": 1})
    assert "_notice" not in injector.apply(3, "mail_search", {"x": 1})
