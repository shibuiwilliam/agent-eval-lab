"""記録／再生カセット。キーはリクエストの正規 JSON の sha256。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from agenteval.core.normalize import sha256_of
from agenteval.llm.cost import REPO_ROOT

# 塩ありの鍵のハッシュ入力の最上位キー。リクエスト本文がこの 2 キーだけを持つことは無いので、塩なしの鍵と衝突しない
SALT_FIELD: Final = "__cassette_salt__"

CASSETTE_ROOT = REPO_ROOT / "data" / "cassettes" / "llm"
FIXTURE_ROOT = REPO_ROOT / "data" / "fixtures" / "cassettes" / "llm"


class CassetteMiss(LookupError):
    """カセットに無かった。分岐再実行では「旧軌跡から分岐した」信号として使う。"""


def cassette_key(request: dict[str, Any], salt: str | None = None) -> str:
    """リクエスト本文からキーを作る。metadata やリクエスト ID は含めない。

    salt is None: `sha256_of(request)`（従来と同一。既存カセットはすべてこの鍵）。
    salt あり:    `sha256_of({SALT_FIELD: salt, "request": request})`。反復ごとに別の鍵にする（ADR-038）。
    salt == "" は ValueError（塩なしとの取り違えを防ぐ）。
    """
    if salt is None:
        return sha256_of(request)
    if salt == "":
        raise ValueError("salt は None か非空文字列。空文字列は塩なしと取り違えるので受け付けない")
    return sha256_of({SALT_FIELD: salt, "request": request})


def cassette_path(model: str, key: str, root: Path | None = None) -> Path:
    """保存先パス `<root>/<model>/<hash[:2]>/<hash>.json`。"""
    base = root or CASSETTE_ROOT
    return base / model.replace("/", "_") / key[:2] / f"{key}.json"


def load(model: str, key: str, roots: list[Path] | None = None) -> dict[str, Any]:
    """カセットを読む。見つからなければ CassetteMiss。"""
    for root in roots or [CASSETTE_ROOT, FIXTURE_ROOT]:
        path = cassette_path(model, key, root)
        if path.exists():
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return data
    raise CassetteMiss(f"cassette miss: model={model} key={key[:12]}")


def save(
    model: str,
    key: str,
    request: dict[str, Any],
    response: dict[str, Any],
    usage: dict[str, int],
    sdk_version: str,
    root: Path | None = None,
    salt: str | None = None,
) -> Path:
    """カセットを書く。API キーは request 本文に含まれないので保存して安全。

    salt が None でなければ payload に "salt" キーを足す（監査用。`request` は API に送った本文のまま）。
    None なら payload は従来と同一。
    """
    path = cassette_path(model, key, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "request": request,
        "response": response,
        "usage": usage,
        "recorded_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "sdk_version": sdk_version,
    }
    if salt is not None:
        if salt == "":
            raise ValueError("salt は None か非空文字列")
        payload["salt"] = salt
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
