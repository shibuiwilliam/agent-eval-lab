"""軌跡リンター（原典 4.4）。時相論理アサーションを関数合成で書く。パーサは作らない。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agenteval.core.events import Event
from agenteval.core.schema import Violation

Pred = Callable[[Event], bool]
Check = Callable[[Sequence[Event]], list[Violation]]


@dataclass(frozen=True)
class Rule:
    """規則 1 個。"""

    id: str
    description: str
    check: Check

    def run(self, events: Sequence[Event]) -> list[Violation]:
        return self.check(events)


# --- 述語 -----------------------------------------------------------------
def is_call(tool: str | None = None) -> Pred:
    def pred(e: Event) -> bool:
        return e.kind == "call" and (tool is None or e.tool == tool)

    return pred


def is_error_result(tool: str | None = None) -> Pred:
    def pred(e: Event) -> bool:
        return e.kind == "result" and e.is_error and (tool is None or e.tool == tool)

    return pred


def is_finish() -> Pred:
    def pred(e: Event) -> bool:
        return e.kind == "finish"

    return pred


def arg_eq(key: str, value: object) -> Pred:
    def pred(e: Event) -> bool:
        return e.args.get(key) == value

    return pred


def both(a: Pred, b: Pred) -> Pred:
    return lambda e: a(e) and b(e)


# --- 演算子 ---------------------------------------------------------------
def always(pred: Pred, rule_id: str, detail: str) -> Check:
    """全イベントが pred を満たすこと。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        return [
            Violation(rule_id=rule_id, step=e.step, detail=detail) for e in events if not pred(e)
        ]

    return check


def once_before(target: Pred, required: Pred, rule_id: str, detail: str) -> Check:
    """target の各出現より前に required が一度はあること。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        out: list[Violation] = []
        seen = False
        for e in events:
            if required(e):
                seen = True
            elif target(e) and not seen:
                out.append(Violation(rule_id=rule_id, step=e.step, detail=detail))
        return out

    return check


def not_until(forbidden: Pred, required: Pred, rule_id: str, detail: str) -> Check:
    """required が起きるまで forbidden を行わないこと。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        out: list[Violation] = []
        unlocked = False
        for e in events:
            if required(e):
                unlocked = True
            elif forbidden(e) and not unlocked:
                out.append(Violation(rule_id=rule_id, step=e.step, detail=detail))
        return out

    return check


def count_le(pred: Pred, n: int, rule_id: str, detail: str) -> Check:
    """pred を満たすイベントが n 個以下であること。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        hits = [e for e in events if pred(e)]
        if len(hits) <= n:
            return []
        return [
            Violation(rule_id=rule_id, step=e.step, detail=f"{detail}（{len(hits)} 回）")
            for e in hits[n:]
        ]

    return check


def next_after(trigger: Pred, allowed: Pred, rule_id: str, detail: str) -> Check:
    """trigger の直後のイベントは allowed を満たすこと。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        out: list[Violation] = []
        for i, e in enumerate(events[:-1]):
            if trigger(e) and not allowed(events[i + 1]):
                out.append(Violation(rule_id=rule_id, step=e.step, detail=detail))
        return out

    return check


def eventually(pred: Pred, rule_id: str, detail: str) -> Check:
    """pred を満たすイベントが少なくとも 1 つあること。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        if any(pred(e) for e in events):
            return []
        return [Violation(rule_id=rule_id, step=-1, detail=detail)]

    return check


def pairwise_required(
    target_tool: str, required_tool: str, key: str, rule_id: str, detail: str
) -> Check:
    """target_tool(key=x) の前に required_tool(key=x) があること（キー単位の once_before）。"""

    def check(events: Sequence[Event]) -> list[Violation]:
        out: list[Violation] = []
        done: set[object] = set()
        for e in events:
            if e.kind != "call":
                continue
            value = e.args.get(key)
            if e.tool == required_tool:
                done.add(value)
            elif e.tool == target_tool and value not in done:
                out.append(Violation(rule_id=rule_id, step=e.step, detail=f"{detail}: {value}"))
        return out

    return check


def lint(events: Sequence[Event], rules: Sequence[Rule]) -> list[Violation]:
    """規則集を適用する。"""
    out: list[Violation] = []
    for rule in rules:
        out.extend(rule.run(events))
    return out
