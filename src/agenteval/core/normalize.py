"""引数と JSON の正規化。分岐判定・重複判定・同値類はすべてここを通す。

`normalize_args` は 1 箇所にしか存在しない（.claude/rules/env-agent.md）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any

_WS = re.compile(r"\s+")
_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y年%m月%d日",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
)


def canonical_json(obj: Any) -> str:
    """キー順を固定した JSON 文字列。ハッシュ入力に使う。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_of(obj: Any) -> str:
    """任意のオブジェクトの正規 JSON の sha256。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def normalize_datetime(value: str) -> str:
    """よくある日付書式を ISO 8601 に寄せる。解釈できなければ元の文字列を返す。"""
    text = value.strip()
    for pattern in _DATE_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
        if "%H" in pattern:
            return parsed.strftime("%Y-%m-%dT%H:%M")
        return parsed.strftime("%Y-%m-%d")
    return text


def normalize_scalar(value: Any) -> Any:
    """スカラ 1 個の正規化。文字列は空白圧縮・小文字化・日時 ISO 化。"""
    if isinstance(value, str):
        text = _WS.sub(" ", value).strip()
        text = normalize_datetime(text)
        return text.lower()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def normalize_args(args: dict[str, Any]) -> dict[str, Any]:
    """ツール引数の正規化。キー順、空白除去、日時 ISO 化、小文字化。

    None 値のキーは「指定しなかった」と同じ扱いにして落とす（版間で既定値の有無が
    変わっても同値と見なせるようにするため）。
    """
    out: dict[str, Any] = {}
    for key in sorted(args):
        value = args[key]
        if value is None:
            continue
        if isinstance(value, dict):
            out[key] = normalize_args(value)
        elif isinstance(value, list):
            out[key] = [
                normalize_args(v) if isinstance(v, dict) else normalize_scalar(v) for v in value
            ]
        else:
            out[key] = normalize_scalar(value)
    return out


def action_key(tool_name: str, args: dict[str, Any]) -> str:
    """行動の同値類キー（ツール名 ＋ 正規化引数）。"""
    return f"{tool_name}:{canonical_json(normalize_args(args))}"
