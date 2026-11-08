"""summary の「読み方（設計者の要約）」節（W6-I1）。実ページの内容には依存せず、tmp の写しで確かめる。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.reports import verify


def _row(experiment_id: str) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "chapter": "C",
        "title": "t",
        "verdict": "PASS",
        "detail": "",
        "provisional": False,
        "provenance": "live",
        "usd": 0.0,
        "key_metrics": [],
    }


TABLE = [_row("EX-1"), _row("EX-2"), _row("EX-3")]
PAGE_WITH = "# EX-1\n\n## 判定\n\n自動判定\n\nsummary 用の要約: PASS。ただし H2 は未検証。\n\n## 気づき・限界\n"
PAGE_WITHOUT = "# EX-2\n\n## 判定\n\nPASS\n"
# 「## 判定」以外の節にある行は読まない
PAGE_OTHER_SECTION = "# EX-3\n\n## 気づき・限界\n\nsummary 用の要約: 拾ってはいけない\n"


def test_page_reading_is_read_only_from_the_verdict_section(tmp_path: Path) -> None:
    (tmp_path / "EX-1.md").write_text(PAGE_WITH, encoding="utf-8")
    (tmp_path / "EX-2.md").write_text(PAGE_WITHOUT, encoding="utf-8")
    (tmp_path / "EX-3.md").write_text(PAGE_OTHER_SECTION, encoding="utf-8")
    assert verify.load_readings_ja(TABLE, tmp_path) == {"EX-1": "PASS。ただし H2 は未検証。"}


def test_ja_section_lists_only_pages_with_a_reading(tmp_path: Path) -> None:
    readings = {"EX-1": "PASS。ただし H2 は未検証。"}
    text = verify.write_summary(TABLE, tmp_path / "s.md", readings=readings).read_text(
        encoding="utf-8"
    )
    assert "## 読み方（設計者の要約）" in text
    assert "- [EX-1](EX-1.md): PASS。ただし H2 は未検証。" in text
    assert "EX-2.md):" not in text and "EX-3.md):" not in text
    # 節は表・注記の後、総コストの前
    assert text.index("## 読み方") < text.index("総コスト")
    assert text.index("## 読み方") > text.index("章 C")


def test_ja_section_absent_when_no_reading(tmp_path: Path) -> None:
    for readings in (None, {}):
        text = verify.write_summary(TABLE, tmp_path / "s.md", readings=readings).read_text(
            encoding="utf-8"
        )
        assert "読み方" not in text
        assert "総コスト" in text


def test_ja_section_follows_registry_order(tmp_path: Path) -> None:
    readings = {"EX-3": "c", "EX-1": "a"}
    text = verify.write_summary(TABLE, tmp_path / "s.md", readings=readings).read_text(
        encoding="utf-8"
    )
    assert text.index("- [EX-1]") < text.index("- [EX-3]")


def test_en_uses_summary_reading_only() -> None:
    english = {
        "EX-1": {"id": "EX-1", "title": "T1", "summary_reading": "PASS, but H2 is untested."},
        "EX-2": {"id": "EX-2", "title": "T2"},
        "EX-3": {"id": "EX-3", "summary_reading": "  "},
    }
    assert verify.load_readings_en(TABLE, english) == {"EX-1": "PASS, but H2 is untested."}


def test_en_section_and_absence(tmp_path: Path) -> None:
    text = verify.write_summary_en(
        TABLE, tmp_path / "s.en.md", readings={"EX-1": "PASS, but H2 is untested."}
    ).read_text(encoding="utf-8")
    assert "## How to read (designer's summary)" in text
    assert "- [EX-1](EX-1.en.md): PASS, but H2 is untested." in text
    assert text.index("## How to read") < text.index("Total cost")
    empty = verify.write_summary_en(TABLE, tmp_path / "e.en.md", readings={}).read_text(
        encoding="utf-8"
    )
    assert "How to read" not in empty
