"""13 個のツール。schema は pydantic モデルから生成し、手書きしない。

説明文は「いつ使う／使わない・引数の意味・返さないもの」を 3〜4 文で書く
（.claude/rules/env-agent.md）。応答は高信号フィールドだけを返す。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agenteval.core.normalize import canonical_json
from agenteval.env.office import OfficeEnv

Toolset = Literal["v1", "v2"]


# --- 引数モデル -----------------------------------------------------------
class CalendarSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, description="件名や参加者名の部分一致")
    date_from: str | None = Field(default=None, description="開始日 YYYY-MM-DD")
    date_to: str | None = Field(default=None, description="終了日 YYYY-MM-DD")


class CalendarSearchArgsV2(BaseModel):
    """v06_toolschema_v2 の引数（`query`→`keyword`、`date_*`→`range_*`）。"""

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(default=None, description="件名や参加者名の部分一致")
    range_start: str | None = Field(default=None, description="開始日 YYYY-MM-DD")
    range_end: str | None = Field(default=None, description="終了日 YYYY-MM-DD")


class CalendarCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="予定の件名")
    start: str = Field(description="開始日時 YYYY-MM-DDTHH:MM")
    end: str = Field(description="終了日時 YYYY-MM-DDTHH:MM")
    room: str | None = Field(default=None, description="会議室名（A / B など）")
    attendees: list[str] = Field(default_factory=list, description="参加者の contact id")


class CalendarDeleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(description="削除する予定の id")


class MailSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, description="件名・本文の部分一致")
    sender: str | None = Field(default=None, description="差出人のメールアドレス")
    limit: int = Field(default=5, description="返す最大件数")


class ContactsSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="氏名・メールアドレス・部署の部分一致")


class MailSendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(description="宛先メールアドレス")
    subject: str = Field(description="件名")
    body: str = Field(description="本文")


class FileReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="読み出すファイルパス")


class FileWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="書き込むファイルパス")
    content: str = Field(description="書き込む内容（全置換）")


class FileDeleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="削除するファイルパス")


class FileBackupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="バックアップ元のファイルパス")


class ChecksRunArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str | None = Field(
        default=None, description="calendar / mail / files のいずれか。省略で全部"
    )


class ExternalLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="参照キー（room-a, projector など）")


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    tool: str
    depends_on: list[str] = Field(default_factory=list)


class SubmitPlanArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(description="実行予定のステップの有向非巡回グラフ")


class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="利用者向けの結果要約")
    claims: list[str] = Field(default_factory=list, description="やったと主張することの列挙")


DESCRIPTIONS: dict[str, str] = {
    "calendar_search": (
        "カレンダーの予定を検索する。予定を作る前の空き確認や、既存予定の重複確認に使う。"
        "query は件名・参加者名の部分一致、date_from / date_to は日付範囲を絞る。"
        "予定の中身（参加者の空き状況やメール本文）は返さないので、必要なら別のツールを使う。"
    ),
    "calendar_create": (
        "カレンダーに予定を作る。空き確認を済ませてから呼ぶこと。"
        "start / end は YYYY-MM-DDTHH:MM 形式、room は会議室名、attendees は contact id の配列。"
        "重複していても作成はできるので、重複の検査は calendar_search か checks_run で行う。"
        "既存予定の変更はできない（削除して作り直す）。"
    ),
    "calendar_delete": (
        "予定を削除する。依頼で明示的に取り消しを求められた時だけ使う。"
        "event_id は calendar_search が返した id。削除は取り消せない。"
        "予定の内容は返さないので、消す前に calendar_search で確認すること。"
    ),
    "contacts_search": (
        "連絡先を検索してメールアドレスを調べる。メールを送る前に、宛先のアドレスを確かめるために使う。"
        "query は氏名・メールアドレス・部署の部分一致で、「田中」「営業」のような短い語で引ける。"
        "一致した連絡先の id・氏名・メールアドレス・部署を返す。予定やメールの履歴は返さない。"
    ),
    "mail_search": (
        "受信メールを検索する。依頼の背景や相手の要望を確認するために使う。"
        "query は件名・本文の部分一致、sender は差出人アドレス、limit は最大件数。"
        "本文は先頭 200 文字までしか返さない。全文が要るときは query を絞って再検索する。"
    ),
    "mail_send": (
        "メールを送る。宛先が連絡先に存在することを確認してから使う。"
        "to は宛先アドレス、subject は件名、body は本文。送信は取り消せない。"
        "送信控えは mails テーブルに残るが、相手の返信は返らない。"
    ),
    "file_read": (
        "ファイルの内容を読む。編集や引用の前に必ず読む。"
        "path はリポジトリ相対のパス。存在しないパスはエラーを返す。"
        "ディレクトリ一覧は返さないので、パスが不明なら mail_search などで手がかりを探す。"
    ),
    "file_write": (
        "ファイルを書く（全置換）。既存ファイルを編集するときは file_read で現在の内容を読んでから呼ぶ。"
        "path と content を渡す。差分適用ではないので、content には完成形を渡すこと。"
        "バックアップは自動で取られない。"
    ),
    "file_delete": (
        "ファイルを削除する。依頼で削除が明示されている時だけ使う。"
        "path を渡す。削除は取り消せないので、事前に file_backup を取ること。"
        "削除したファイルの内容は返らない。"
    ),
    "file_backup": (
        "ファイルのバックアップを取る。破壊的な変更（削除・全置換）の前に使う。"
        "path を渡すと backups テーブルに写しが作られる。"
        "バックアップからの復元はこのツールではできない（file_write で書き戻す）。"
    ),
    "checks_run": (
        "作業結果の検査を走らせる。予定の重複、宛先不明のメール、参照切れファイルを調べる。"
        "scope で calendar / mail / files に絞れる。省略すると全部を調べる。"
        "問題の修正はしない。検出された問題は自分で直してから完了を宣言すること。"
    ),
    "external_lookup": (
        "外部サービスに単価や設備情報を問い合わせる。見積りや費用の記載が必要なときに使う。"
        "key は room-a、projector などの参照キー。"
        "応答の項目名はサービス側の版によって変わることがあるので、返ってきたキーをそのまま読むこと。"
    ),
    "submit_plan": (
        "これから行う手順を計画として提出する。最初の 1 回だけ使う。"
        "steps は id / description / tool / depends_on を持つステップの配列で、依存関係は非巡回にする。"
        "計画は実行されない。提出後に実際のツールを呼ぶこと。"
    ),
    "finish": (
        "作業の完了を宣言する。すべての依頼事項を満たし、検査を終えてから呼ぶ。"
        "summary は利用者向けの要約、claims はやったと主張することの列挙。"
        "呼んだ時点で run は終了する。未完了のまま呼ばないこと。"
    ),
}

ARG_MODELS: dict[str, type[BaseModel]] = {
    "calendar_search": CalendarSearchArgs,
    "calendar_create": CalendarCreateArgs,
    "calendar_delete": CalendarDeleteArgs,
    "mail_search": MailSearchArgs,
    "contacts_search": ContactsSearchArgs,
    "mail_send": MailSendArgs,
    "file_read": FileReadArgs,
    "file_write": FileWriteArgs,
    "file_delete": FileDeleteArgs,
    "file_backup": FileBackupArgs,
    "checks_run": ChecksRunArgs,
    "external_lookup": ExternalLookupArgs,
    "submit_plan": SubmitPlanArgs,
    "finish": FinishArgs,
}

TOOL_NAMES: list[str] = list(ARG_MODELS)
WRITE_TOOLS = {"calendar_create", "calendar_delete", "mail_send", "file_write", "file_delete"}


def arg_model(name: str, toolset: Toolset = "v1") -> type[BaseModel]:
    """ツール名 → 引数モデル。v2 では calendar_search の引数が変わる。"""
    if toolset == "v2" and name == "calendar_search":
        return CalendarSearchArgsV2
    return ARG_MODELS[name]


def _force_additional_properties_false(schema: dict[str, Any]) -> None:
    """schema 内のすべての object に `additionalProperties: false` を付ける。

    `strict: true` は入れ子の object（`$defs` の中を含む）にもこれを要求する
    （live で `tools.11.custom: For 'object' type, 'additionalProperties' must be explicitly
    set to false` を確認済み）。
    """
    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
    for key in ("properties", "$defs", "definitions"):
        for value in (schema.get(key) or {}).values():
            if isinstance(value, dict):
                _force_additional_properties_false(value)
    items = schema.get("items")
    if isinstance(items, dict):
        _force_additional_properties_false(items)


def tool_schemas(toolset: Toolset = "v1", strict: bool | None = None) -> list[dict[str, Any]]:
    """Anthropic の tools 定義。pydantic の JSON Schema をそのまま使う。

    `strict` を付けると schema 準拠が保証される（Haiku 4.5 / Sonnet 5 の両方で利用可能。
    既定は `models.USE_STRICT_TOOLS`）。
    """
    from agenteval.llm.models import USE_STRICT_TOOLS

    use_strict = USE_STRICT_TOOLS if strict is None else strict
    out: list[dict[str, Any]] = []
    for name in TOOL_NAMES:
        schema = arg_model(name, toolset).model_json_schema()
        schema.pop("title", None)
        entry: dict[str, Any] = {
            "name": name,
            "description": DESCRIPTIONS[name],
            "input_schema": schema,
        }
        if use_strict:
            _force_additional_properties_false(schema)
            entry["strict"] = True
        out.append(entry)
    return out


def toolset_hash_material(toolset: Toolset = "v1") -> str:
    """版ハッシュに入れるツール schema の正規表現。"""
    return canonical_json(tool_schemas(toolset))


# --- 実行 -----------------------------------------------------------------
def _overlaps(
    env: OfficeEnv, start: str, end: str, room: str | None, skip_id: str = ""
) -> list[dict[str, Any]]:
    rows = env.query("SELECT * FROM events")
    hits = []
    for row in rows:
        if row["id"] == skip_id:
            continue
        overlaps = row["start"] < end and start < row["end"]
        if overlaps and (room is None or (row["room"] or "") == room):
            hits.append(row)
    return hits


def exec_calendar_search(env: OfficeEnv, args: dict[str, Any], toolset: Toolset) -> dict[str, Any]:
    query = args.get("keyword") if toolset == "v2" else args.get("query")
    lo = args.get("range_start") if toolset == "v2" else args.get("date_from")
    hi = args.get("range_end") if toolset == "v2" else args.get("date_to")
    rows = env.query("SELECT * FROM events ORDER BY start")
    out = []
    for row in rows:
        if query and query not in (row["title"] or "") and query not in (row["attendees"] or ""):
            continue
        if lo and row["start"][:10] < lo:
            continue
        if hi and row["start"][:10] > hi:
            continue
        out.append({k: row[k] for k in ("id", "title", "start", "end", "room")})
    return {"events": out, "count": len(out)}


def exec_calendar_create(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    eid = env.next_id("events", "e")
    attendees = ",".join(args.get("attendees") or [])
    env.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
        (eid, args["title"], args["start"], args["end"], args.get("room") or "", attendees, ""),
    )
    env.record_write("event", eid, "calendar_create")
    conflicts = _overlaps(env, args["start"], args["end"], args.get("room"), skip_id=eid)
    return {"id": eid, "created": True, "conflicts": len(conflicts)}


def exec_calendar_delete(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    rows = env.query("SELECT * FROM events WHERE id = ?", (args["event_id"],))
    if not rows:
        return {"error": "not_found", "event_id": args["event_id"]}
    env.execute("DELETE FROM events WHERE id = ?", (args["event_id"],))
    env.record_write("event", args["event_id"], "calendar_delete")
    return {"deleted": True, "event_id": args["event_id"]}


def exec_mail_search(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    rows = env.query("SELECT * FROM mails ORDER BY ts DESC")
    out = []
    for row in rows:
        if args.get("query") and args["query"] not in (row["subject"] or "") + (row["body"] or ""):
            continue
        if args.get("sender") and args["sender"] != row["sender"]:
            continue
        out.append(
            {
                "id": row["id"],
                "sender": row["sender"],
                "subject": row["subject"],
                "body": (row["body"] or "")[:200],
                "ts": row["ts"],
            }
        )
        if len(out) >= int(args.get("limit") or 5):
            break
    return {"mails": out, "count": len(out)}


def exec_contacts_search(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    rows = env.query("SELECT * FROM contacts ORDER BY id")
    out = [
        row
        for row in rows
        if query in (row["name"] or "")
        or query in (row["email"] or "")
        or query in (row["dept"] or "")
        or query.replace(" ", "") in (row["name"] or "").replace(" ", "")
    ]
    return {"contacts": out, "count": len(out)}


def exec_mail_send(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    known = {r["email"] for r in env.query("SELECT email FROM contacts")}
    mid = env.next_id("mails", "m")
    env.execute(
        "INSERT INTO mails VALUES (?,?,?,?,?,?,?)",
        (
            mid,
            "me@example.co.jp",
            args["to"],
            args["subject"],
            args["body"],
            env.today + "T12:00",
            "sent",
        ),
    )
    env.record_write("mail", mid, "mail_send")
    return {"id": mid, "sent": True, "recipient_known": args["to"] in known}


def exec_file_read(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    rows = env.query("SELECT * FROM files WHERE path = ?", (args["path"],))
    if not rows:
        return {"error": "not_found", "path": args["path"]}
    return {"path": args["path"], "content": rows[0]["content"]}


def exec_file_write(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    exists = env.query("SELECT path FROM files WHERE path = ?", (args["path"],))
    if exists:
        env.execute(
            "UPDATE files SET content = ?, updated_at = ? WHERE path = ?",
            (args["content"], env.today + "T12:00", args["path"]),
        )
    else:
        env.execute(
            "INSERT INTO files VALUES (?,?,?)",
            (args["path"], args["content"], env.today + "T12:00"),
        )
    env.record_write("file", args["path"], "file_write")
    return {"path": args["path"], "written": True, "bytes": len(args["content"])}


def exec_file_delete(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    rows = env.query("SELECT * FROM files WHERE path = ?", (args["path"],))
    if not rows:
        return {"error": "not_found", "path": args["path"]}
    env.execute("DELETE FROM files WHERE path = ?", (args["path"],))
    env.record_write("file", args["path"], "file_delete")
    return {"deleted": True, "path": args["path"]}


def exec_file_backup(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    rows = env.query("SELECT * FROM files WHERE path = ?", (args["path"],))
    if not rows:
        return {"error": "not_found", "path": args["path"]}
    bid = env.next_id("backups", "b")
    env.execute(
        "INSERT INTO backups VALUES (?,?,?,?)",
        (bid, args["path"], rows[0]["content"], env.today + "T12:00"),
    )
    env.record_write("backup", args["path"], "file_backup")
    return {"backup_id": bid, "path": args["path"]}


def exec_checks_run(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    scope = args.get("scope")
    issues: list[dict[str, Any]] = []
    if scope in (None, "calendar"):
        rows = env.query("SELECT * FROM events ORDER BY start")
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                if (
                    a["start"] < b["end"]
                    and b["start"] < a["end"]
                    and (a["room"] or "") == (b["room"] or "")
                    and a["room"]
                ):
                    issues.append(
                        {"kind": "overlap", "events": [a["id"], b["id"]], "room": a["room"]}
                    )
    if scope in (None, "mail"):
        known = {r["email"] for r in env.query("SELECT email FROM contacts")}
        for row in env.query("SELECT * FROM mails WHERE folder = 'sent'"):
            if row["recipient"] not in known:
                issues.append(
                    {"kind": "unknown_recipient", "mail": row["id"], "to": row["recipient"]}
                )
    if scope in (None, "files"):
        paths = {r["path"] for r in env.query("SELECT path FROM files")}
        for row in env.query("SELECT * FROM files"):
            for token in (row["content"] or "").split():
                if token.endswith(".md") and "/" in token and token not in paths:
                    issues.append({"kind": "dangling_ref", "path": row["path"], "ref": token})
    return {"ok": not issues, "issues": issues[:10], "issue_count": len(issues)}


def exec_external_lookup(env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    return env.external.lookup(args["key"])


def exec_submit_plan(_env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    steps = args.get("steps") or []
    return {"accepted": True, "steps": len(steps)}


def exec_finish(_env: OfficeEnv, args: dict[str, Any], _ts: Toolset) -> dict[str, Any]:
    return {"acknowledged": True, "claims": len(args.get("claims") or [])}


EXECUTORS: dict[str, Callable[[OfficeEnv, dict[str, Any], Toolset], dict[str, Any]]] = {
    "calendar_search": exec_calendar_search,
    "calendar_create": exec_calendar_create,
    "calendar_delete": exec_calendar_delete,
    "mail_search": exec_mail_search,
    "contacts_search": exec_contacts_search,
    "mail_send": exec_mail_send,
    "file_read": exec_file_read,
    "file_write": exec_file_write,
    "file_delete": exec_file_delete,
    "file_backup": exec_file_backup,
    "checks_run": exec_checks_run,
    "external_lookup": exec_external_lookup,
    "submit_plan": exec_submit_plan,
    "finish": exec_finish,
}


class ToolError(Exception):
    """引数検証に失敗した（`tool_result.is_error = True` で返す）。"""


def execute(
    env: OfficeEnv, name: str, args: dict[str, Any], toolset: Toolset = "v1"
) -> dict[str, Any]:
    """ツールを 1 回実行する。引数は pydantic で検証する。"""
    if name not in EXECUTORS:
        raise ToolError(f"未知のツール: {name}")
    model = arg_model(name, toolset)
    validated = model.model_validate(args)
    return EXECUTORS[name](env, validated.model_dump(), toolset)
