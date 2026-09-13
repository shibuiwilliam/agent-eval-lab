"""正規化の単体テスト（API を呼ばない）。"""

from __future__ import annotations

from agenteval.core.normalize import action_key, canonical_json, normalize_args, sha256_of


def test_key_order_is_canonical() -> None:
    assert normalize_args({"b": 1, "a": 2}) == {"a": 2, "b": 1}
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_whitespace_and_case() -> None:
    assert normalize_args({"x": "  A  B "}) == {"x": "a b"}


def test_dates_are_iso() -> None:
    assert normalize_args({"d": "2026/09/21"}) == {"d": "2026-09-21"}
    assert normalize_args({"d": "2026年9月21日"}) == {"d": "2026-09-21"}
    assert normalize_args({"d": "2026-09-21T10:00"}) == {"d": "2026-09-21t10:00"}


def test_none_keys_are_dropped() -> None:
    assert normalize_args({"a": None, "b": 1}) == {"b": 1}


def test_action_key_is_stable() -> None:
    a = action_key("file_read", {"path": "/A/b.txt"})
    b = action_key("file_read", {"path": "/a/B.txt"})
    assert a == b


def test_sha256_is_deterministic() -> None:
    assert sha256_of({"a": 1}) == sha256_of({"a": 1})
    assert sha256_of({"a": 1}) != sha256_of({"a": 2})
