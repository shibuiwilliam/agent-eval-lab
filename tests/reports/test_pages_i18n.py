"""結果ページの日英 2 言語出力（`reports.pages` と `reports.i18n`）。

数値・判定・基準は言語に依存しない、という不変条件を固定する。
API は呼ばない（conftest が replay を強制する）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agenteval.reports import i18n, pages

META: dict[str, Any] = {
    "id": "E9-9",
    "title": "日本語のタイトル",
    "hypothesis": "日本語の仮説",
    "planted_text": "日本語の植込み",
    "control_text": "日本語の対照",
    "provenance": "simulated",
    "criteria": [{"metric": "score", "op": ">=", "value": 0.5}],
}
RESULT: dict[str, Any] = {
    "metrics": {"score": {"value": 0.75, "provenance": "simulated"}},
    "figures": [("図の見出し", "fig/E9-9_x.png")],
    "notes": ["日本語の気づき"],
    "method": "日本語の方法",
    "verdict": "PASS",
    "verdict_reason": "全基準を満たした。",
    "ran_at": "2026-09-19T00:00:00+00:00",
    "usd": 0.0,
}
EN: dict[str, Any] = {
    "id": "E9-9",
    "title": "English title",
    "hypothesis": "English hypothesis",
    "planted": "English planted",
    "control": "English control",
    "method": "English method",
    "verdict_reason": "All criteria were met.",
    "notes": ["English note"],
    "figures": ["English caption"],
}


def test_english_page_replaces_prose_but_not_numbers() -> None:
    """英語版は文章だけを差し替え、数値・判定・基準・図のパスは日本語版と同じ。"""
    ja = pages.render(META, RESULT)
    en = pages.render(META, RESULT, "en", EN)
    for text in ("English title", "English method", "English note", "English caption"):
        assert text in en
    assert "日本語" not in en.replace("*日本語版:", "")
    for shared in ("0.75 [simulated]", ">= 0.5", "PASS", "fig/E9-9_x.png"):
        assert shared in ja
        assert shared in en


def test_english_page_falls_back_to_japanese_for_missing_keys() -> None:
    """英訳が欠けた項目は日本語のまま残す（未訳を目に見えるようにする）。"""
    en = pages.render(META, RESULT, "en", {"id": "E9-9", "title": "English title"})
    assert "English title" in en
    assert "日本語の方法" in en


def test_write_emits_both_languages_only_when_translation_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """英訳があれば 2 ファイル、無ければ日本語 1 ファイルだけを書く。"""
    monkeypatch.setattr(i18n, "load_en", lambda path=None: {})
    monkeypatch.setattr(pages.i18n, "load_en", lambda path=None: {})
    assert [p.name for p in pages.write(META, RESULT, tmp_path)] == ["E9-9.md"]

    monkeypatch.setattr(pages.i18n, "load_en", lambda path=None: {"E9-9": EN})
    written = pages.write(META, RESULT, tmp_path)
    assert [p.name for p in written] == ["E9-9.md", "E9-9.en.md"]
    assert "English title" in (tmp_path / "E9-9.en.md").read_text(encoding="utf-8")


def test_registry_en_covers_every_experiment() -> None:
    """`registry.en.yaml` が全実験を網羅し、注記の数が日本語側と一致する。"""
    from agenteval.reports.verify import load_registry, load_result

    english = i18n.load_en()
    for entry in load_registry():
        assert entry["id"] in english, f"英訳が無い: {entry['id']}"
        result = load_result(entry["id"])
        if result is None:
            continue
        assert len(english[entry["id"]]["notes"]) == len(result["notes"]), (
            f"注記の数が日本語と合わない: {entry['id']}"
        )


def test_registry_en_is_valid_yaml_with_required_keys() -> None:
    """英訳エントリに必要な鍵がそろっている。"""
    entries = yaml.safe_load(i18n.EN_REGISTRY_PATH.read_text(encoding="utf-8"))
    required = {"id", "title", "hypothesis", "planted", "control", "method", "notes"}
    for entry in entries:
        assert required <= set(entry), f"{entry.get('id')} に足りない鍵: {required - set(entry)}"
