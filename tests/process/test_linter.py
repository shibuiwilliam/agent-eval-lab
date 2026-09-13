"""軌跡リンターの単体テスト。手書きイベント列で全演算子を網羅し、性質は hypothesis で検査する。"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from agenteval.core.events import Event
from agenteval.core.registry import get_task
from agenteval.process.linter import (
    always,
    count_le,
    eventually,
    is_call,
    is_error_result,
    lint,
    next_after,
    not_until,
    once_before,
    pairwise_required,
)
from agenteval.process.rules.global_rules import GLOBAL_RULES, build_rules


def call(tool: str, step: int = 0, **args: object) -> Event:
    return Event(kind="call", step=step, tool=tool, args=dict(args))


def result(tool: str, step: int = 0, is_error: bool = False) -> Event:
    return Event(kind="result", step=step, tool=tool, is_error=is_error)


def finish(step: int = 9) -> Event:
    return Event(kind="finish", step=step, tool="finish")


def test_always() -> None:
    check = always(lambda e: e.tool != "file_delete", "r", "d")
    assert not check([call("file_read")])
    assert len(check([call("file_delete")])) == 1


def test_once_before() -> None:
    check = once_before(is_call("file_delete"), is_call("file_backup"), "r", "d")
    assert not check([call("file_backup"), call("file_delete")])
    assert len(check([call("file_delete"), call("file_backup")])) == 1


def test_not_until() -> None:
    check = not_until(is_call("mail_send"), is_call("mail_search"), "r", "d")
    assert not check([call("mail_search"), call("mail_send")])
    assert len(check([call("mail_send")])) == 1


def test_count_le() -> None:
    check = count_le(is_call("calendar_search"), 2, "r", "d")
    assert not check([call("calendar_search")] * 2)
    assert len(check([call("calendar_search")] * 4)) == 2


def test_next_after() -> None:
    check = next_after(is_error_result("file_read"), is_call("file_read"), "r", "d")
    assert not check([result("file_read", is_error=True), call("file_read")])
    assert len(check([result("file_read", is_error=True), call("file_delete")])) == 1


def test_eventually() -> None:
    check = eventually(is_call("checks_run"), "r", "d")
    assert not check([call("checks_run")])
    assert len(check([call("file_read")])) == 1


def test_pairwise_required_is_key_scoped() -> None:
    check = pairwise_required("file_delete", "file_backup", "path", "r", "d")
    ok = [call("file_backup", path="a"), call("file_delete", path="a")]
    ng = [call("file_backup", path="a"), call("file_delete", path="b")]
    assert not check(ok)
    assert len(check(ng)) == 1


def test_claim_done_requires_checks() -> None:
    rules = [r for r in GLOBAL_RULES if r.id == "claim_done_requires_checks"]
    without = [call("file_write", path="x"), finish()]
    with_check = [call("file_write", path="x"), call("checks_run"), finish()]
    assert len(lint(without, rules)) == 1
    assert not lint(with_check, rules)


def test_claim_done_requires_checks_fires_without_finish() -> None:
    """live で判明した偽陰性への回帰テスト（IMPROVEMENT.md L1）。

    実エージェントは finish を呼ばずに終わることが多い。finish が無くても
    「書き込んだのに検査していない」を検出できること。
    """
    rules = [r for r in GLOBAL_RULES if r.id == "claim_done_requires_checks"]
    no_finish = [call("file_write", path="x"), result("file_write")]
    assert len(lint(no_finish, rules)) == 1
    with_check = [call("file_write", path="x"), call("checks_run")]
    assert not lint(with_check, rules)


def test_declare_completion_with_finish() -> None:
    rules = [r for r in GLOBAL_RULES if r.id == "declare_completion_with_finish"]
    assert len(lint([call("file_read", path="x")], rules)) == 1
    assert not lint([call("file_read", path="x"), finish()], rules)
    assert not lint([], rules)


def test_task_forbidden_rule_is_compiled() -> None:
    rules = build_rules(get_task("T-001"))
    ids = {r.id for r in rules}
    assert "never_call:calendar_delete" in ids
    assert lint([call("calendar_delete", event_id="e1")], rules)


@given(
    prefix=st.lists(st.sampled_from(["file_read", "calendar_search", "checks_run"]), max_size=5),
    path=st.sampled_from(["a.md", "b.md"]),
)
def test_backup_inserted_before_delete_never_violates(prefix: list[str], path: str) -> None:
    """delete の直前に backup を挿入した列は no_delete_without_backup に違反しない。"""
    events = [call(t) for t in prefix] + [
        call("file_backup", path=path),
        call("file_delete", path=path),
    ]
    rules = [r for r in GLOBAL_RULES if r.id == "no_delete_without_backup"]
    assert not lint(events, rules)


@given(prefix=st.lists(st.sampled_from(["file_read", "calendar_search"]), max_size=5))
def test_delete_without_backup_always_violates(prefix: list[str]) -> None:
    events = [call(t) for t in prefix] + [call("file_delete", path="a.md")]
    rules = [r for r in GLOBAL_RULES if r.id == "no_delete_without_backup"]
    assert len(lint(events, rules)) == 1
