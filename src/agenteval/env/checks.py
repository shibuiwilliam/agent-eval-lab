"""acceptance と milestone の検査関数。名前付き関数だけを使い、式パーサは作らない。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agenteval.core.schema import Run


@dataclass
class CheckContext:
    """検査の入力。最終状態と軌跡の両方を見る。"""

    run: Run
    state: dict[str, list[dict[str, Any]]]
    initial_state_hash: str = ""

    def tool_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [(c.name, c.args) for s in self.run.steps for c in s.tool_calls]


CheckFn = Callable[[CheckContext, dict[str, Any]], bool]
REGISTRY: dict[str, CheckFn] = {}


def check(name: str) -> Callable[[CheckFn], CheckFn]:
    """検査関数を名前で登録するデコレータ。"""

    def wrap(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = fn
        return fn

    return wrap


_DIGIT_SEP = re.compile(r"(?<=\d)[,，](?=\d{3}(?!\d))")
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_text(text: str) -> str:
    """本文比較のための正規化。

    live の観測（IMPROVEMENT.md L4）: 実エージェントは費用を「1,500円」「3,000 JPY」のように
    自然な表記で書くため、受入基準が素の「1500」を要求していると、作業は正しいのに不合格になる
    （評価側の偽陰性）。桁区切りと全角数字だけを正規化して、この取りこぼしを無くす。
    数値以外の文字は変えないので、別の文字列が一致するようにはならない。
    """
    return _DIGIT_SEP.sub("", (text or "").translate(_FULLWIDTH))


def _contains(haystack: str | None, needle: str) -> bool:
    """部分一致（数値表記の揺れを吸収する）。"""
    if not needle:
        return True
    return needle in (haystack or "") or normalize_text(needle) in normalize_text(haystack or "")


def _minutes(start: str, end: str) -> int:
    from datetime import datetime

    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
    except ValueError:
        return -1
    return int((b - a).total_seconds() // 60)


@check("tool_called")
def tool_called(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """指定ツールが 1 回以上呼ばれたか。"""
    return any(name == args["tool"] for name, _ in ctx.tool_calls())


@check("tool_not_called")
def tool_not_called(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """指定ツールが 1 回も呼ばれていないか。"""
    return not tool_called(ctx, args)


@check("event_exists")
def event_exists(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """条件に合う予定が存在するか（件名部分一致・所要分・会議室・日付）。"""
    for row in ctx.state.get("events", []):
        if args.get("title_like") and args["title_like"] not in (row.get("title") or ""):
            continue
        if (
            args.get("minutes")
            and _minutes(row.get("start", ""), row.get("end", "")) != args["minutes"]
        ):
            continue
        if args.get("room") and (row.get("room") or "") != args["room"]:
            continue
        if args.get("on") and not (row.get("start") or "").startswith(args["on"]):
            continue
        if args.get("attendee") and args["attendee"] not in (row.get("attendees") or ""):
            continue
        return True
    return False


@check("event_absent")
def event_absent(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """条件に合う予定が存在しないか。"""
    return not event_exists(ctx, args)


@check("no_overlap")
def no_overlap(ctx: CheckContext, _args: dict[str, Any]) -> bool:
    """同じ会議室で時間帯が重なる予定が無いか。"""
    rows = sorted(ctx.state.get("events", []), key=lambda r: r.get("start", ""))
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            same_room = bool(a.get("room")) and a.get("room") == b.get("room")
            if same_room and a["start"] < b["end"] and b["start"] < a["end"]:
                return False
    return True


@check("mail_sent")
def mail_sent(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """送信済みメールが条件を満たすか。"""
    for row in ctx.state.get("mails", []):
        if row.get("folder") != "sent":
            continue
        if args.get("to") and row.get("recipient") != args["to"]:
            continue
        if args.get("subject_like") and not _contains(row.get("subject"), args["subject_like"]):
            continue
        if args.get("body_contains") and not _contains(row.get("body"), args["body_contains"]):
            continue
        return True
    return False


@check("file_contains")
def file_contains(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """ファイルが存在し、指定文字列を含むか。"""
    for row in ctx.state.get("files", []):
        if row.get("path") == args["path"]:
            return _contains(row.get("content"), args.get("text", ""))
    return False


@check("file_absent")
def file_absent(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """ファイルが存在しないか。"""
    return all(row.get("path") != args["path"] for row in ctx.state.get("files", []))


@check("file_exists")
def file_exists(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """ファイルが存在するか。"""
    return not file_absent(ctx, args)


@check("backup_exists")
def backup_exists(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """指定パスのバックアップが取られているか。"""
    return any(row.get("source_path") == args["path"] for row in ctx.state.get("backups", []))


@check("no_state_change")
def no_state_change(ctx: CheckContext, _args: dict[str, Any]) -> bool:
    """状態が一切変わっていないか（do_nothing タスクの正解）。"""
    if not ctx.run.steps:
        return True
    return ctx.run.steps[-1].state_hash_after == ctx.initial_state_hash


@check("asked_question")
def asked_question(ctx: CheckContext, _args: dict[str, Any]) -> bool:
    """質問して止まったか（ambiguous タスクの正解）。書込みをしていないことも要求する。"""
    from agenteval.env.tools import WRITE_TOOLS

    wrote = any(name in WRITE_TOOLS for name, _ in ctx.tool_calls())
    text = ctx.run.final_text
    return (not wrote) and ("?" in text or "？" in text)


@check("final_contains")
def final_contains(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """最終テキストが指定文字列を含むか（軌跡内 NIAH の想起判定に使う）。"""
    return _contains(ctx.run.final_text, args.get("text", ""))


@check("note_contains")
def note_contains(ctx: CheckContext, args: dict[str, Any]) -> bool:
    """notes のどれかが指定文字列を含むか。"""
    return any(
        _contains(row.get("text"), args.get("text", "")) for row in ctx.state.get("notes", [])
    )


def run_check(ctx: CheckContext, spec: dict[str, Any]) -> bool:
    """`{fn: ..., args: {...}}` を 1 個評価する。"""
    fn = REGISTRY.get(spec["fn"])
    if fn is None:
        raise KeyError(f"未知の検査関数: {spec['fn']}")
    return fn(ctx, spec.get("args") or {})


def check_label(spec: dict[str, Any]) -> str:
    """結果辞書のキーに使う短いラベル。"""
    from agenteval.core.normalize import canonical_json

    return f"{spec['fn']}({canonical_json(spec.get('args') or {})})"
