"""模擬忠実度と TTL（原典 8.5）。外部サービスのカセットとライブ比較。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from agenteval.env.external import ExternalService
from agenteval.llm.cost import REPO_ROOT

EXTERNAL_CASSETTE_DIR = REPO_ROOT / "data" / "cassettes" / "external"

# 層化アサーション（原典 8.7）: 厳密一致 → 状態検査 → 意味的判定 の順に適用する
LAYERS = ("exact", "structural", "semantic")


@dataclass
class ExternalCassette:
    """外部サービスのカセット 1 件。"""

    key: str
    recorded_at: str
    ttl_days: int
    response: dict[str, Any]

    def expired(self, today: date | None = None) -> bool:
        ref = today or datetime.now(tz=UTC).date()
        return date.fromisoformat(self.recorded_at) + timedelta(days=self.ttl_days) < ref


@dataclass
class FidelityResult:
    """忠実度ジョブの結果。"""

    fidelity: float
    checked: int
    mismatched_keys: set[str] = field(default_factory=set)
    by_layer: dict[str, int] = field(default_factory=dict)
    expired: list[str] = field(default_factory=list)


def record_cassettes(
    service: ExternalService,
    ttl_days: int = 30,
    recorded_at: str = "2026-09-13",
    directory: Path | None = None,
) -> list[Path]:
    """現在の外部サービスの応答をカセットとして保存する。"""
    target = directory or EXTERNAL_CASSETTE_DIR
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for key in service.available_keys():
        payload = {
            "key": key,
            "recorded_at": recorded_at,
            "ttl_days": ttl_days,
            "response": service.lookup(key),
        }
        path = target / f"{key}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def load_cassettes(directory: Path | None = None) -> list[ExternalCassette]:
    """保存済みカセットを読む。"""
    target = directory or EXTERNAL_CASSETTE_DIR
    if not target.exists():
        return []
    out = []
    for path in sorted(target.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(ExternalCassette(**data))
    return out


def compare(recorded: dict[str, Any], live: dict[str, Any]) -> tuple[bool, str, set[str]]:
    """層化アサーション。どの層で一致したかと、不一致キーを返す。"""
    if recorded == live:
        return True, "exact", set()
    if set(recorded) == set(live):
        mismatched = {k for k in recorded if recorded[k] != live[k]}
        return False, "structural", mismatched
    mismatched = set(recorded) ^ set(live)
    return False, "semantic", mismatched


def fidelity_job(
    service_now: ExternalService, directory: Path | None = None, today: date | None = None
) -> FidelityResult:
    """期限切れと未期限を両方ライブ比較し、`F` と不一致キー一覧を出す。"""
    cassettes = load_cassettes(directory)
    if not cassettes:
        return FidelityResult(fidelity=1.0, checked=0)
    matched = 0
    mismatched: set[str] = set()
    by_layer: dict[str, int] = dict.fromkeys(LAYERS, 0)
    expired = []
    for cassette in cassettes:
        live = service_now.lookup(cassette.key)
        ok, layer, keys = compare(cassette.response, live)
        by_layer[layer] += 1
        matched += int(ok)
        mismatched |= keys
        if cassette.expired(today):
            expired.append(cassette.key)
    return FidelityResult(
        fidelity=round(matched / len(cassettes), 4),
        checked=len(cassettes),
        mismatched_keys=mismatched,
        by_layer=by_layer,
        expired=expired,
    )


def expired_detection_rate(directory: Path | None = None, today: date | None = None) -> float:
    """TTL 超過の検出率（期限切れカセットのうち検出できた割合）。"""
    cassettes = load_cassettes(directory)
    if not cassettes:
        return 1.0
    ref = today or datetime.now(tz=UTC).date()
    truly_expired = [
        c for c in cassettes if date.fromisoformat(c.recorded_at) + timedelta(days=c.ttl_days) < ref
    ]
    if not truly_expired:
        return 1.0
    detected = [c for c in truly_expired if c.expired(ref)]
    return round(len(detected) / len(truly_expired), 4)
