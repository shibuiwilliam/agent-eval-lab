"""ツールの単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.env import tools
from agenteval.env.fixtures import load_sql
from agenteval.env.office import OfficeEnv


@pytest.fixture
def env(tmp_path: Path) -> OfficeEnv:
    return OfficeEnv.create(tmp_path / "e.sqlite", load_sql("office_small_v1"), run_id="t")


def test_fourteen_tools_with_schemas() -> None:
    schemas = tools.tool_schemas()
    assert len(schemas) == 14
    assert all(s["input_schema"]["type"] == "object" for s in schemas)
    assert all(len(s["description"]) > 60 for s in schemas)


def test_toolset_v2_renames_calendar_search_args() -> None:
    v1 = {s["name"]: s for s in tools.tool_schemas("v1")}["calendar_search"]
    v2 = {s["name"]: s for s in tools.tool_schemas("v2")}["calendar_search"]
    assert "query" in v1["input_schema"]["properties"]
    assert "keyword" in v2["input_schema"]["properties"]
    assert tools.toolset_hash_material("v1") != tools.toolset_hash_material("v2")


def test_old_arg_names_fail_under_v2(env: OfficeEnv) -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic の ValidationError
        tools.execute(env, "calendar_search", {"query": "定例"}, "v2")


def test_checks_run_detects_overlap(env: OfficeEnv) -> None:
    tools.execute(
        env,
        "calendar_create",
        {"title": "x", "start": "2026-09-21T10:00", "end": "2026-09-21T10:30", "room": "A"},
    )
    result = tools.execute(env, "checks_run", {"scope": "calendar"})
    assert result["ok"] is False
    assert result["issues"][0]["kind"] == "overlap"


def test_checks_run_detects_unknown_recipient(env: OfficeEnv) -> None:
    tools.execute(env, "mail_send", {"to": "nobody@example.com", "subject": "s", "body": "b"})
    result = tools.execute(env, "checks_run", {"scope": "mail"})
    assert any(i["kind"] == "unknown_recipient" for i in result["issues"])


def test_contacts_search_finds_by_name_and_dept(env: OfficeEnv) -> None:
    """live で判明した穴（連絡先を引く手段が無い）への回帰テスト（IMPROVEMENT.md L2）。"""
    by_name = tools.execute(env, "contacts_search", {"query": "高橋"})
    assert by_name["count"] == 1
    assert by_name["contacts"][0]["email"] == "takahashi@example.co.jp"
    assert tools.execute(env, "contacts_search", {"query": "開発"})["count"] == 2
    assert tools.execute(env, "contacts_search", {"query": "存在しない"})["count"] == 0


def test_backup_then_delete(env: OfficeEnv) -> None:
    tools.execute(env, "file_backup", {"path": "tmp/scratch.txt"})
    tools.execute(env, "file_delete", {"path": "tmp/scratch.txt"})
    assert env.query("SELECT * FROM backups")
    assert not env.query("SELECT * FROM files WHERE path='tmp/scratch.txt'")


def test_acceptance_tolerates_number_formatting() -> None:
    """live で判明した偽陰性への回帰テスト（IMPROVEMENT.md L4）。

    実エージェントは「1,500円」と書くが、受入基準は「1500」を要求していた。
    """
    from agenteval.env.checks import normalize_text

    assert normalize_text("単価：1,500円") == "単価：1500円"
    assert normalize_text("3,000 JPY") == "3000 JPY"
    assert normalize_text("１２３４５") == "12345"
    # 桁区切りでないカンマは残す（別の文字列が一致するようにはしない）
    assert normalize_text("a,b") == "a,b"
    assert normalize_text("1,2") == "1,2"
