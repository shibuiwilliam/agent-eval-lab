"""決定的シミュレート・エージェント（ADR-008）。

live API が使えない環境で、評価手法の検証に必要な軌跡を作るための LLM スタンドイン。
方針:
  - 挙動は **版の system prompt の section** から読み取る（LLM がプロンプトに従うのと同じ経路）
  - タスクの意図は `Task.sim`（意図）から取る。**acceptance は見ない**ので、意図どおり動いても
    合否は独立に決まる
  - 乱れ（誤り）は (task, seed, repeat) に固定した「運」ベクトルから引く。版をまたいで同じ運を
    使うので、版の比較は対応のある比較になる
このモジュールが作る数値の来歴ラベルは常に `simulated`。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from agenteval.core.normalize import sha256_of
from agenteval.core.registry import Task
from agenteval.llm.client import MessageRequest, MessageResult
from agenteval.llm.models import MODEL_IDS

SECTION_RE = re.compile(r"<!--\s*section:\s*([a-z0-9_]+)\s*-->")


@dataclass
class SimFlags:
    """system prompt から読み取った挙動フラグ。"""

    search_before_write: bool = True  # 版によらず True（下の from_prompt のコメントを参照）
    verify_before_finish: bool = True
    backup_before_delete: bool = True
    clarify_when_ambiguous: bool = True
    respect_scope: bool = True
    brevity: bool = False
    repeat_search: bool = False
    slash_dates: bool = False
    plan_override: bool = False
    contaminated: bool = False

    @classmethod
    def from_prompt(cls, prompt: str) -> SimFlags:
        """section の有無と本文から挙動を決める。"""
        bodies: dict[str, str] = {}
        marks = list(SECTION_RE.finditer(prompt))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(prompt)
            bodies[m.group(1)] = prompt[m.end() : end].strip()
        return cls(
            # live で確認（IMPROVEMENT.md L5）: `workflow` section を落とした v07 でも、
            # 実エージェントは file_read / calendar_search を省かない。
            # 「編集前に読む」「作る前に空きを確認する」はツールの説明文自体に書いてあり、
            # それはどの版にも共通だからである。省かれるのは checks_run（verification）だけだった。
            # そのため、行動に必要な 1 回の情報取得は版によらず行う。
            search_before_write=True,
            verify_before_finish="verification" in bodies,
            backup_before_delete="safety" in bodies,
            clarify_when_ambiguous="clarify" in bodies,
            respect_scope="scope" in bodies,
            brevity="efficiency" in bodies,
            repeat_search="thoroughness" in bodies,
            slash_dates="YYYY/MM/DD" in bodies.get("date_format", ""),
            plan_override="plan_override" in bodies,
            contaminated="examples" in bodies,
        )


@dataclass
class Luck:
    """(task, seed, repeat) に固定した乱れ。版をまたいで同じ値を使う。"""

    mistake: float
    mistake_kind: int
    schema_adapt: float
    recall: float
    external_adapt: float
    do_anyway: float

    @classmethod
    def draw(cls, task_id: str, seed: int, repeat: int) -> Luck:
        digest = sha256_of([task_id, seed, repeat])
        rng = np.random.default_rng(int(digest[:16], 16) % (2**63))
        return cls(
            mistake=float(rng.random()),
            mistake_kind=int(rng.integers(0, 3)),
            schema_adapt=float(rng.random()),
            recall=float(rng.random()),
            external_adapt=float(rng.random()),
            do_anyway=float(rng.random()),
        )


@dataclass
class HistoryItem:
    """会話から復元した 1 回のツール呼び出しと結果。"""

    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    is_error: bool


@dataclass
class Simulator:
    """1 run 分のシミュレート・エージェント。"""

    task: Task
    seed: int = 0
    repeat: int = 0
    max_steps: int = 25
    _luck: Luck = field(init=False)

    def __post_init__(self) -> None:
        self._luck = Luck.draw(self.task.id, self.seed, self.repeat)

    # --- 公開 API ---------------------------------------------------------
    def respond(self, req: MessageRequest) -> MessageResult:
        """1 ステップぶんの応答を返す。"""
        prompt = (
            req.system
            if isinstance(req.system, str)
            else "".join(b.get("text", "") for b in req.system)
        )
        flags = SimFlags.from_prompt(prompt)
        competence = 0.5 if req.model == MODEL_IDS["agent_swap"] else 0.0
        if flags.contaminated and self.task.visibility == "private":
            # few-shot に答えが混入している版は private タスクだけ急に解けるようになる（8.9）
            competence = max(competence, 0.9)
        history = self._history(req.messages)
        schema_v2 = self._is_schema_v2(req.tools)
        forced = (req.tool_choice or {}).get("name") if req.tool_choice else None

        if forced == "submit_plan":
            action = ("submit_plan", self._plan_steps(flags))
        else:
            action = self._decide(flags, competence, history, schema_v2, req.messages)

        name, args = action
        text = self._narrate(name, args)
        if name == "__end_turn__":
            return self._result([{"type": "text", "text": text}], "end_turn", req)
        block = {
            "type": "tool_use",
            "id": f"toolu_sim_{len(history):03d}",
            "name": name,
            "input": args,
        }
        return self._result([{"type": "text", "text": text}, block], "tool_use", req)

    # --- 状態復元 ---------------------------------------------------------
    def _history(self, messages: list[dict[str, Any]]) -> list[HistoryItem]:
        calls: list[tuple[str, dict[str, Any], str]] = []
        results: dict[str, tuple[dict[str, Any], bool]] = {}
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    calls.append((block["name"], block.get("input", {}), block["id"]))
                elif block.get("type") == "tool_result":
                    raw = block.get("content")
                    text = (
                        raw
                        if isinstance(raw, str)
                        else "".join(c.get("text", "") for c in raw or [] if isinstance(c, dict))
                    )
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {"raw": text}
                    results[block["tool_use_id"]] = (parsed, bool(block.get("is_error")))
        out = []
        for name, args, cid in calls:
            parsed, is_error = results.get(cid, ({}, False))
            out.append(HistoryItem(tool=name, args=args, result=parsed, is_error=is_error))
        return out

    @staticmethod
    def _is_schema_v2(tools: list[dict[str, Any]]) -> bool:
        for tool in tools:
            if tool.get("name") == "calendar_search":
                return "keyword" in (tool.get("input_schema", {}).get("properties") or {})
        return False

    # --- 方策 -------------------------------------------------------------
    def _decide(
        self,
        flags: SimFlags,
        competence: float,
        history: list[HistoryItem],
        schema_v2: bool,
        messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        intent = self.task.sim
        called = [h.tool for h in history]
        if len(history) >= self.max_steps - 1:
            return self._finish(flags, history, messages)

        # 曖昧タスク: 確認して止まるのが正解
        if self.task.kind == "ambiguous":
            if flags.clarify_when_ambiguous and self._luck.do_anyway > 0.15:
                return (
                    "finish",
                    {
                        "summary": f"確認させてください。{intent.query} どちらの解釈でよいですか？",
                        "claims": [],
                    },
                )
            return self._act(flags, competence, history, schema_v2)

        # 検索してからでないと「既に満たされている」ことが分からない
        if self.task.kind == "do_nothing":
            if flags.search_before_write and not called:
                return self._search_call(schema_v2, flags)
            already_ok = any(
                h.tool.endswith("_search") and h.result.get("count", 0) > 0 for h in history
            )
            if already_ok or (flags.search_before_write and self._luck.do_anyway > 0.25):
                return (
                    "finish",
                    {
                        "summary": "確認したところ、依頼の内容はすでに満たされていました。変更は行っていません。",
                        "claims": ["現状を確認した"],
                    },
                )
            return self._act(flags, competence, history, schema_v2)

        return self._act(flags, competence, history, schema_v2)

    def _act(
        self,
        flags: SimFlags,
        competence: float,
        history: list[HistoryItem],
        schema_v2: bool,
    ) -> tuple[str, dict[str, Any]]:
        called = [h.tool for h in history]
        write_tools = {
            "calendar_create",
            "calendar_delete",
            "mail_send",
            "file_write",
            "file_delete",
        }
        if self.task.sim.action == "backup_file":
            write_tools = {"file_backup"}
        wrote = any(h.tool in write_tools for h in history)

        # 1) 検索・読み取り
        if flags.search_before_write and not self._did_lookup(history):
            return self._search_call(schema_v2, flags)
        # v04_loopy: 同じ検索をもう一度なぞる
        if flags.repeat_search and called.count(self._lookup_tool()) < 2 and not wrote:
            return self._search_call(schema_v2, flags)
        # v06: 誤った引数名で失敗したら直して再試行
        if history and history[-1].is_error and history[-1].tool == "calendar_search":
            return self._search_call(schema_v2, flags, force_correct=True)

        # 1.5) 宛先が要る行動では、送る前に連絡先を引いてアドレスを確かめる
        if (
            flags.search_before_write
            and self.task.sim.to
            and "contacts_search" not in called
            and not wrote
        ):
            return ("contacts_search", {"query": self._contact_query()})

        # 2) 書き込み（検査で重複が見つかって消した場合は、空き時間に作り直す）
        recreate = self._needs_recreate(history)
        if recreate is not None:
            return recreate
        if not wrote:
            return self._write_call(flags, competence, history)

        # 3) 検証と修復（計画を無視する版と、ステップ数最小化を指示された版は検査を飛ばす）
        verify = flags.verify_before_finish and not (flags.plan_override or flags.brevity)
        if verify and "checks_run" not in called:
            return ("checks_run", {"scope": self._scope()})
        issues = self._last_issues(history)
        if issues and verify and not self._repaired(history):
            repair = self._repair_call(issues, history)
            if repair is not None:
                return repair
        return self._finish(flags, history, [])

    def _did_lookup(self, history: list[HistoryItem]) -> bool:
        """検索・読み取りが済んだか。2 回失敗したら諦めて先に進む（実際に起きる挙動）。"""
        tool = self._lookup_tool()
        attempts = [h for h in history if h.tool == tool]
        if any(not h.is_error for h in attempts):
            return True
        return len(attempts) >= 2

    def _contact_query(self) -> str:
        """宛先アドレスの手前部分を連絡先検索の語にする（`sato@...` → `sato`）。"""
        return (self.task.sim.to or "").split("@")[0]

    def _lookup_tool(self) -> str:
        action = self.task.sim.action
        if action in ("create_event", "delete_event", "do_nothing"):
            return "calendar_search"
        if action in ("send_mail", "recall", "ask"):
            return "mail_search"
        if action == "lookup_cost":
            return "external_lookup"
        return "file_read"

    def _scope(self) -> str | None:
        action = self.task.sim.action
        if action in ("create_event", "delete_event"):
            return "calendar"
        if action == "send_mail":
            return "mail"
        if action in ("edit_file", "delete_file"):
            return "files"
        return None

    def _search_call(
        self, schema_v2: bool, flags: SimFlags, force_correct: bool = False
    ) -> tuple[str, dict[str, Any]]:
        tool = self._lookup_tool()
        intent = self.task.sim
        if tool == "calendar_search":
            day = self._day(intent.day_offset)
            # v06: 新しい引数名への適応。初回に正しく呼べるのは 40%、再試行でさらに戻るが、
            # 45% は最後まで適応できずに検索を諦める（`_did_lookup` の 2 回ルールで先に進む）
            threshold = 0.45 if force_correct else 0.6
            adapt = self._luck.schema_adapt > threshold
            if schema_v2 and adapt:
                return (tool, {"keyword": intent.query or "", "range_start": day, "range_end": day})
            return (tool, {"query": intent.query or "", "date_from": day, "date_to": day})
        if tool == "mail_search":
            return (tool, {"query": intent.query or intent.subject, "limit": 5})
        if tool == "external_lookup":
            return (tool, {"key": intent.key})
        return ("file_read", {"path": intent.path})

    def _write_call(
        self, flags: SimFlags, competence: float, history: list[HistoryItem]
    ) -> tuple[str, dict[str, Any]]:
        """書き込みを 1 回行う。誤りは行動ごとに意味のあるものを 1 つだけ起こす。"""
        intent = self.task.sim
        action = intent.action
        mistake = self._luck.mistake < intent.difficulty * (1.0 - competence)
        kind = self._luck.mistake_kind

        if action == "create_event":
            day_offset = intent.day_offset - 1 if (mistake and kind == 0) else intent.day_offset
            room = None if (mistake and kind == 1) else intent.room
            minutes = intent.minutes * 2 if (mistake and kind == 2) else intent.minutes
            hour = self._free_hour(history, intent.hour, room, self._day(day_offset))
            start = f"{self._day(day_offset)}T{hour:02d}:00"
            end_hour = hour + (minutes // 60)
            end_min = minutes % 60
            end = f"{self._day(day_offset)}T{end_hour:02d}:{end_min:02d}"
            if flags.slash_dates:
                start = start.replace("-", "/")
                end = end.replace("-", "/")
            args: dict[str, Any] = {"title": intent.title, "start": start, "end": end}
            if room:
                args["room"] = room
            if intent.attendee:
                args["attendees"] = [intent.attendee]
            return ("calendar_create", args)

        if action == "delete_event":
            eid = self._found_event_id(history) or intent.event_id
            if mistake and kind == 0:
                eid = "e001"  # 別の予定を消してしまう
            return ("calendar_delete", {"event_id": eid})

        if action == "send_mail":
            to = "unknown@example.co.jp" if (mistake and kind == 0) else (intent.to or "")
            subject = "（無題）" if (mistake and kind == 2) else intent.subject
            body = intent.body
            if intent.text and not (mistake and kind == 1):
                body = f"{body}\n{intent.text}"
            return ("mail_send", {"to": to, "subject": subject, "body": body})

        if action == "edit_file":
            base = self._read_content(history, intent.path)
            path = intent.path
            text = intent.text
            if mistake and kind == 0:
                base = ""  # 既存の記述を読まずに全置換してしまう
            elif mistake and kind == 1:
                path = f"{intent.path}.bak"  # 書き先を間違える
            elif mistake and kind == 2:
                text = intent.text[: max(1, len(intent.text) // 2)]  # 途中までしか書かない
            content = f"{base}\n{text}\n" if base else text
            return ("file_write", {"path": path, "content": content})

        if action == "delete_file":
            if flags.backup_before_delete and not any(h.tool == "file_backup" for h in history):
                return ("file_backup", {"path": intent.path})
            path = "docs/spec.md" if (mistake and kind == 0) else intent.path
            return ("file_delete", {"path": path})

        if action == "backup_file":
            return ("file_backup", {"path": intent.path})

        if action == "lookup_cost":
            price = self._price(history)
            if mistake and kind in (0, 2):
                price = "不明"
            body = f"{intent.body}\n費用: {price}"
            if intent.to:
                to = "unknown@example.co.jp" if (mistake and kind == 1) else intent.to
                return ("mail_send", {"to": to, "subject": intent.subject, "body": body})
            path = f"{intent.path}.tmp" if (mistake and kind == 1) else intent.path
            return ("file_write", {"path": path, "content": body})

        if action == "recall":
            return ("finish", {"summary": self._recall_text(history), "claims": ["調査した"]})

        return ("finish", {"summary": "対応しました。", "claims": []})

    # --- 補助 -------------------------------------------------------------
    def _day(self, offset: int) -> str:
        from datetime import UTC, datetime, timedelta

        from agenteval.env.fixtures import BASE_DATE

        base = datetime.fromisoformat(BASE_DATE).replace(tzinfo=UTC)
        return (base + timedelta(days=offset)).strftime("%Y-%m-%d")

    def _free_hour(self, history: list[HistoryItem], want: int, room: str | None, day: str) -> int:
        """検索結果から空き時間を選ぶ。検索していなければ希望時刻をそのまま使う。"""
        events: list[dict[str, Any]] = []
        for item in history:
            if item.tool == "calendar_search" and not item.is_error:
                events = item.result.get("events", []) or []
        if not events:
            return want
        busy = set()
        for ev in events:
            if room and (ev.get("room") or "") != room:
                continue
            start = str(ev.get("start", ""))
            if start.startswith(day) and "T" in start:
                busy.add(int(start.split("T")[1][:2]))
        hour = want
        for _ in range(6):
            if hour not in busy:
                return hour
            hour += 1
        return hour

    def _found_event_id(self, history: list[HistoryItem]) -> str | None:
        for item in history:
            if item.tool == "calendar_search" and item.result.get("events"):
                return str(item.result["events"][0]["id"])
        return None

    def _read_content(self, history: list[HistoryItem], path: str) -> str:
        for item in history:
            if item.tool == "file_read" and item.args.get("path") == path:
                return str(item.result.get("content", ""))
        return ""

    def _price(self, history: list[HistoryItem]) -> str:
        """外部サービスの応答から価格を読む。版が上がってキー名が変わると失敗しうる。"""
        for item in history:
            if item.tool != "external_lookup":
                continue
            result = item.result
            if "price" in result:
                return str(result["price"])
            if "unit_price" in result and self._luck.external_adapt < 0.5:
                return str(result["unit_price"])
            return "不明"
        return "不明"

    def _recall_text(self, history: list[HistoryItem]) -> str:
        """軌跡内 NIAH。ノイズで薄まると key_fact を落とす。"""
        fact = self.task.key_fact or ""
        blob = json.dumps([h.result for h in history], ensure_ascii=False)
        if fact and fact not in blob:
            return "確認しましたが、該当する情報は見つかりませんでした。"
        noise = sum(len(str(h.result.get("_notice", ""))) for h in history)
        ratio = noise / max(1, len(blob))
        p_recall = float(np.clip(1.0 - 0.85 * ratio, 0.05, 0.98))
        if self._luck.recall <= p_recall:
            return f"確認しました。{fact} です。"
        return "確認しましたが、詳細までは特定できませんでした。"

    def _last_issues(self, history: list[HistoryItem]) -> list[dict[str, Any]]:
        for item in reversed(history):
            if item.tool == "checks_run":
                return list(item.result.get("issues") or [])
        return []

    def _repaired(self, history: list[HistoryItem]) -> bool:
        seen_check = False
        for item in history:
            if item.tool == "checks_run":
                seen_check = True
            elif seen_check and item.tool in {
                "calendar_delete",
                "calendar_create",
                "mail_send",
                "file_write",
            }:
                return True
        return False

    def _repair_call(
        self, issues: list[dict[str, Any]], history: list[HistoryItem]
    ) -> tuple[str, dict[str, Any]] | None:
        """検査で見つかった問題を直す（重複予定をずらす／宛先を直す）。"""
        for issue in issues:
            if issue.get("kind") == "overlap":
                created = self._own_event(history)
                if created:
                    return ("calendar_delete", {"event_id": created})
            if issue.get("kind") == "unknown_recipient":
                intent = self.task.sim
                return (
                    "mail_send",
                    {"to": intent.to or "", "subject": intent.subject, "body": intent.body},
                )
        return None

    def _needs_recreate(self, history: list[HistoryItem]) -> tuple[str, dict[str, Any]] | None:
        """重複解消で自分の予定を消したあと、まだ作り直していないなら作り直す。"""
        deleted_at = None
        for index, item in enumerate(history):
            if item.tool == "calendar_delete" and item.result.get("deleted"):
                deleted_at = index
        if deleted_at is None or self.task.sim.action != "create_event":
            return None
        if any(h.tool == "calendar_create" for h in history[deleted_at + 1 :]):
            return None
        created = [h for h in history[:deleted_at] if h.tool == "calendar_create"]
        if not created:
            return None
        args = dict(created[-1].args)
        start = str(args.get("start", ""))
        if "T" in start:
            day, clock = start.split("T", 1)
            hour = int(clock[:2]) + 1
            minutes = self.task.sim.minutes
            args["start"] = f"{day}T{hour:02d}:00"
            args["end"] = f"{day}T{hour + minutes // 60:02d}:{minutes % 60:02d}"
        return ("calendar_create", args)

    def _own_event(self, history: list[HistoryItem]) -> str | None:
        for item in history:
            if item.tool == "calendar_create" and item.result.get("id"):
                return str(item.result["id"])
        return None

    def _finish(
        self, flags: SimFlags, history: list[HistoryItem], _messages: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        claims = []
        for item in history:
            if item.tool == "calendar_create" and not item.is_error:
                claims.append("予定を作成した")
            if item.tool == "mail_send" and not item.is_error:
                claims.append("メールを送信した")
            if item.tool in ("file_write", "file_delete") and not item.is_error:
                claims.append("ファイルを更新した")
            if item.tool == "checks_run":
                claims.append("検査を実行した")
        if not flags.verify_before_finish:
            # 検証していないのに「確認した」と主張する（自己申告忠実度の観察対象）
            claims.append("問題がないことを確認した")
        if self.task.sim.action == "recall":
            return ("finish", {"summary": self._recall_text(history), "claims": claims})
        summary = "依頼の内容を実施しました。" + (
            "" if not claims else " / ".join(dict.fromkeys(claims))
        )
        return ("finish", {"summary": summary, "claims": list(dict.fromkeys(claims))})

    def _plan_steps(self, flags: SimFlags) -> dict[str, Any]:
        """`submit_plan` に出す計画。"""
        lookup = self._lookup_tool()
        write = {
            "create_event": "calendar_create",
            "delete_event": "calendar_delete",
            "send_mail": "mail_send",
            "edit_file": "file_write",
            "delete_file": "file_delete",
            "backup_file": "file_backup",
            "lookup_cost": "mail_send" if self.task.sim.to else "file_write",
            "do_nothing": "finish",
            "ask": "finish",
            "recall": "finish",
        }[self.task.sim.action]
        steps = [
            {"id": "s1", "description": "現状を確認する", "tool": lookup, "depends_on": []},
            {"id": "s2", "description": "必要な変更を行う", "tool": write, "depends_on": ["s1"]},
            {"id": "s3", "description": "検査する", "tool": "checks_run", "depends_on": ["s2"]},
            {"id": "s4", "description": "完了を宣言する", "tool": "finish", "depends_on": ["s3"]},
        ]
        return {"steps": steps}

    def _narrate(self, name: str, args: dict[str, Any]) -> str:
        if name == "finish":
            return str(args.get("summary", ""))
        return f"{name} を実行します。"

    def _result(
        self, content: list[dict[str, Any]], stop_reason: str, req: MessageRequest
    ) -> MessageResult:
        from agenteval.agent.context import estimate_tokens
        from agenteval.core.schema import Usage

        total_in = estimate_tokens(req.messages, req.system)
        cached = int(total_in * 0.8) if len(req.messages) > 1 else 0
        out_tokens = max(16, len(json.dumps(content, ensure_ascii=False)) // 3)
        usage = Usage(
            input_tokens=total_in - cached,
            output_tokens=out_tokens,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0 if len(req.messages) > 1 else total_in,
        )
        return MessageResult(content=content, stop_reason=stop_reason, usage=usage, source="sim")
