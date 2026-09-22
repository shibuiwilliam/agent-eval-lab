"""外部サービス。環境とは別オブジェクトで、版によって応答形式が変わる（8.5 の被験体）。

v1: `{"item": ..., "price": ..., "currency": ...}`
v2: `price` → `unit_price` に改名し、値も変わる（模擬忠実度 F を下げる植込み）
"""

from __future__ import annotations

from typing import Any, Literal

Version = Literal["v1", "v2"]

# 固定の単価表。v2 では値が 1.1 倍され、キー名が変わる。
_CATALOG: dict[str, dict[str, Any]] = {
    "room-a": {"item": "会議室A", "price": 3000, "currency": "JPY", "capacity": 8},
    "room-b": {"item": "会議室B", "price": 2000, "currency": "JPY", "capacity": 4},
    "projector": {"item": "プロジェクタ", "price": 1500, "currency": "JPY", "capacity": 1},
    "catering": {"item": "ケータリング", "price": 800, "currency": "JPY", "capacity": 20},
    "license-a": {"item": "ライセンスA", "price": 12000, "currency": "JPY", "capacity": 1},
}


class ExternalService:
    """外部の料金参照サービス。`lookup` の応答形式が版で変わる。"""

    def __init__(self, version: Version = "v1") -> None:
        self.version: Version = version

    def lookup(self, key: str) -> dict[str, Any]:
        """キー → 応答。未知キーは `{"error": "not_found"}`。"""
        entry = _CATALOG.get(key.strip().lower())
        if entry is None:
            return {"error": "not_found", "key": key}
        if self.version == "v1":
            return dict(entry)
        renamed = {k: v for k, v in entry.items() if k != "price"}
        renamed["unit_price"] = int(entry["price"] * 1.1)
        return renamed

    def available_keys(self) -> list[str]:
        return sorted(_CATALOG)
