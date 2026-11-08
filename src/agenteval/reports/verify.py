"""レジストリと結果 JSON を突き合わせて PASS / FAIL / NEGATIVE / PENDING を出す。

`criteria` に無い指標で合格を主張しない。来歴ラベルの無い数値は拒否する（ADR-004）。
基準は `applies_if`（指標 演算子 定数）で適用条件を持てる（ADR-045）。条件が偽なら N/A（合格ではない。
判定の AND から外す）、条件の指標が無い・来歴が無いなら FAIL、適用される基準が 0 件でも FAIL。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agenteval.llm.cost import REPO_ROOT
from agenteval.reports import i18n
from agenteval.reports.pages import (
    RESULTS_DIR,
    evaluate_criterion,
    na_text,
    validate_criterion,
)

REGISTRY_PATH = REPO_ROOT / "experiments" / "registry.yaml"
RESULT_DIR = REPO_ROOT / "data" / "results"
SUMMARY_PATH = REPO_ROOT / "docs" / "results" / "summary.md"
SUMMARY_PATH_EN = REPO_ROOT / "docs" / "results" / "summary.en.md"


def load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    """registry.yaml を読む。"""
    data = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data if isinstance(data, list) else data.get("experiments", [])
    for entry in entries:
        for criterion in entry.get("criteria", []):
            validate_criterion(
                criterion, where=f"{entry.get('id')}: "
            )  # 未知のキーを黙って無視しない
    return entries


def is_provisional(entry: dict[str, Any]) -> bool:
    """registry の基準のどれかに `provisional: true` がある実験は暫定扱い（ADR-033）。結果 JSON の有無に依らない。"""
    return any(bool(c.get("provisional")) for c in entry.get("criteria", []))


def load_result(experiment_id: str, directory: Path | None = None) -> dict[str, Any] | None:
    path = (directory or RESULT_DIR) / f"{experiment_id}.json"
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def unlabeled_metrics(result: dict[str, Any]) -> list[str]:
    """来歴ラベルの無い数値を探す（ADR-004 で禁止）。"""
    bad = []
    for key, value in (result.get("metrics") or {}).items():
        if not (isinstance(value, dict) and "provenance" in value):
            bad.append(key)
    return bad


def judge(entry: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """1 実験の判定。"""
    criteria = entry.get("criteria", [])
    if result is None:
        return {"verdict": "PENDING", "detail": "結果 JSON が無い", "criteria": []}
    missing = unlabeled_metrics(result)
    if missing:
        return {
            "verdict": "FAIL",
            "detail": f"来歴ラベルの無い数値: {missing}",
            "criteria": [],
        }
    rows = []
    metrics = result.get("metrics") or {}
    for criterion in criteria:
        ev = evaluate_criterion(criterion, metrics)
        rows.append(
            {
                "metric": criterion["metric"],
                "op": criterion["op"],
                "target": criterion["value"],
                "actual": ev["actual"],
                "ok": None if ev["status"] == "na" else ev["status"] == "pass",
                "status": ev["status"],
                "na": ev["na"],
                "error": ev["error"],
                "applies_if": criterion.get("applies_if"),
                "provisional": bool(criterion.get("provisional")),
            }
        )
    if not rows:
        return {"verdict": "PENDING", "detail": "criteria が空", "criteria": rows}
    forced = result.get("forced_failure")
    if forced:
        # 章 C の実験が、基準の指標では表せない欠陥（例: EC-R2 のカセットの塩の不備）を見つけたとき。基準の達成より優先する
        return {"verdict": "FAIL", "detail": f"強制 FAIL: {forced}", "criteria": rows}
    errors = [f"{r['metric']}（{r['error']}）" for r in rows if r["status"] == "error"]
    if errors:
        # 適用条件の指標が無い・来歴が無い: 実装の不備。failure_type に依らず FAIL
        return {"verdict": "FAIL", "detail": f"適用条件を評価できない: {errors}", "criteria": rows}
    applicable = [r for r in rows if r["status"] != "na"]
    na_note = _na_detail(rows)
    if not applicable:
        # 空虚な合格を作らない
        return {
            "verdict": "FAIL",
            "detail": f"適用される基準が 1 つも無い{na_note}",
            "criteria": rows,
        }
    if all(r["ok"] for r in applicable):
        return {"verdict": "PASS", "detail": f"全基準を満たした{na_note}", "criteria": rows}
    declared = str(result.get("failure_type", "fail")).lower()
    verdict = "NEGATIVE" if declared == "negative" else "FAIL"
    failed = [r["metric"] for r in applicable if not r["ok"]]
    return {"verdict": verdict, "detail": f"未達: {failed}{na_note}", "criteria": rows}


def _na_detail(rows: list[dict[str, Any]]) -> str:
    """N/A の基準があれば、判定の detail に足す文字列（N/A は合格ではない）。"""
    na = [f"{r['metric']}: {na_text(r['na'])}" for r in rows if r["status"] == "na"]
    return f"。N/A（合格ではない）: {'; '.join(na)}" if na else ""


READING_PREFIX = "summary 用の要約:"


def _reading_of_page(text: str) -> str | None:
    """結果ページ本文の「## 判定」節から `summary 用の要約:` の行の本文を取る。無ければ None。"""
    in_verdict = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_verdict = line.strip() == "## 判定"
            continue
        if in_verdict and line.strip().startswith(READING_PREFIX):
            body = line.strip()[len(READING_PREFIX) :].strip()
            return body or None
    return None


def load_readings_ja(table: list[dict[str, Any]], directory: Path | None = None) -> dict[str, str]:
    """実験 ID → 設計者が結果ページに書いた 1 行の読み方（日本語）。ページや行が無い実験は含めない。"""
    base = directory or RESULTS_DIR
    readings: dict[str, str] = {}
    for row in table:
        page = base / f"{row['id']}.md"
        if not page.exists():
            continue
        reading = _reading_of_page(page.read_text(encoding="utf-8"))
        if reading:
            readings[row["id"]] = reading
    return readings


def load_readings_en(
    table: list[dict[str, Any]], english: dict[str, dict[str, Any]] | None = None
) -> dict[str, str]:
    """実験 ID → registry.en.yaml の `summary_reading`。無い実験は含めない（日本語にフォールバックしない）。"""
    source = i18n.load_en() if english is None else english
    readings: dict[str, str] = {}
    for row in table:
        value = source.get(row["id"], {}).get("summary_reading")
        if isinstance(value, str) and value.strip():
            readings[row["id"]] = value.strip()
    return readings


def verify_all(
    registry_path: Path | None = None, result_dir: Path | None = None
) -> list[dict[str, Any]]:
    """全実験を照合し、summary.md を生成する。"""
    table = []
    for entry in load_registry(registry_path):
        result = load_result(entry["id"], result_dir)
        judged = judge(entry, result)
        table.append(
            {
                "id": entry["id"],
                "chapter": entry.get("chapter"),
                "title": entry.get("title", ""),
                "verdict": judged["verdict"],
                "detail": judged["detail"],
                "provisional": is_provisional(entry),
                "provenance": (result or {}).get("provenance", entry.get("provenance", "")),
                "usd": (result or {}).get("usd", 0.0),
                "key_metrics": judged["criteria"],
            }
        )
    write_summary(table, readings=load_readings_ja(table))
    write_summary_en(table, readings=load_readings_en(table))
    return table


def write_summary(
    table: list[dict[str, Any]],
    path: Path | None = None,
    readings: dict[str, str] | None = None,
) -> Path:
    """`docs/results/summary.md` を生成する（手で編集しない）。

    `readings` は実験 ID → 設計者の 1 行の読み方。1 件以上あるときだけ「読み方」節を足す。
    """
    target = path or SUMMARY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 検証サマリ",
        "",
        "*English: [summary.en.md](summary.en.md)*",
        "",
        "このファイルは `uv run agenteval verify` の生成物。手で編集しない。",
        "",
        "| 判定 | 件数 |",
        "|---|---|",
    ]
    lines += _verdict_rows(table)
    lines += [
        "",
        "| 実験 | 章 | タイトル | 判定 | 主要指標 | 来歴 | コスト(USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += _table_rows(table, suffix=".md")
    lines += _chapter_c_footnote(table, lang="ja")
    lines += _na_footnote(table, lang="ja")
    lines += _readings_section(table, readings or {}, lang="ja")
    lines += ["", f"総コスト: {round(sum(r['usd'] for r in table), 4)} USD", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def write_summary_en(
    table: list[dict[str, Any]],
    path: Path | None = None,
    readings: dict[str, str] | None = None,
) -> Path:
    """`docs/results/summary.en.md` を生成する。タイトルは registry.en.yaml から取る。

    `readings` は実験 ID → `summary_reading`（英語）。無い実験は出さない。
    """
    target = path or SUMMARY_PATH_EN
    target.parent.mkdir(parents=True, exist_ok=True)
    english = i18n.load_en()
    lines = [
        "# Verification Summary",
        "",
        "*日本語版: [summary.md](summary.md)*",
        "",
        "This file is generated by `uv run agenteval verify`. Do not edit it by hand.",
        "",
        "| Verdict | Count |",
        "|---|---|",
    ]
    lines += _verdict_rows(table)
    lines += [
        "",
        "| Experiment | Ch. | Title | Verdict | Key metrics | Provenance | Cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += _table_rows(table, suffix=".en.md", titles=english)
    lines += _chapter_c_footnote(table, lang="en")
    lines += _na_footnote(table, lang="en")
    lines += _readings_section(table, readings or {}, lang="en")
    lines += ["", f"Total cost: {round(sum(r['usd'] for r in table), 4)} USD", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _readings_section(
    table: list[dict[str, Any]], readings: dict[str, str], lang: str
) -> list[str]:
    """設計者の 1 行の読み方の節。1 件も無ければ節ごと出さない。registry（table）の順に並べる。"""
    suffix = ".en.md" if lang == "en" else ".md"
    items = [
        f"- [{r['id']}]({r['id']}{suffix}): {readings[r['id']]}"
        for r in table
        if r["id"] in readings
    ]
    if not items:
        return []
    heading = "## How to read (designer's summary)" if lang == "en" else "## 読み方（設計者の要約）"
    return ["", heading, "", *items]


def _chapter_c_footnote(table: list[dict[str, Any]], lang: str) -> list[str]:
    """章 C の行があるときだけ、表の下に注記を足す（既存の章の行は変えない）。

    章 C は第 1 段の 6 実験。結果 JSON が無い間は PENDING で現れる。暫定の基準を持つ実験
    （registry の基準に `provisional: true` がある実験。表の行の `provisional`）を明示する。
    """
    chapter_c = [r["id"] for r in table if str(r["chapter"]) == "C"]
    if not chapter_c:
        return []
    provisional = [r["id"] for r in table if str(r["chapter"]) == "C" and r.get("provisional")]
    if lang == "en":
        text = f"Chapter C = coding agent (NEW_IDEA stage 1, {len(chapter_c)} experiments). "
        if provisional:
            text += (
                "Experiments with a provisional criterion (fixed at G2; ADR-033): "
                f"{', '.join(provisional)}. "
            )
        text += "A row stays PENDING until its result JSON exists."
    else:
        text = f"章 C = コーディングエージェント（NEW_IDEA 第 1 段、{len(chapter_c)} 実験）。"
        if provisional:
            text += f"暫定の基準を持つ実験（G2 で確定する。ADR-033）: {'、'.join(provisional)}。"
        text += "結果 JSON が無い行は PENDING のまま。"
    return ["", f"*{text}*"]


def _verdict_rows(table: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for row in table:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return [f"| {v} | {counts.get(v, 0)} |" for v in ("PASS", "NEGATIVE", "FAIL", "PENDING")]


def _table_rows(
    table: list[dict[str, Any]],
    suffix: str = ".md",
    titles: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    rows = []
    mark = " (prov.)" if suffix == ".en.md" else "（暫定）"
    for row in table:
        lang = "en" if suffix == ".en.md" else "ja"
        metrics = " / ".join(
            f"{m['metric']}={_fmt(m['actual'])}{_mark(m, lang)}{mark if m.get('provisional') else ''}"
            for m in row["key_metrics"]
        )
        title = row["title"]
        if titles is not None:
            title = titles.get(row["id"], {}).get("title", title)
        rows.append(
            f"| [{row['id']}]({row['id']}{suffix}) | {row['chapter']} | {title} | {row['verdict']} "
            f"| {metrics} | {row['provenance']} | {row['usd']} |"
        )
    return rows


def _mark(m: dict[str, Any], lang: str) -> str:
    """基準 1 つの判定の印。N/A は理由つき、適用条件を評価できない基準は ×（理由つき）。"""
    if m.get("status") == "na":
        return f" {na_text(m['na'], lang)}"
    if m.get("status") == "error":
        return f"×（{m['error']}）"
    return "○" if m["ok"] else "×"


def _na_footnote(table: list[dict[str, Any]], lang: str) -> list[str]:
    """N/A の基準がある実験があるときだけ、表の下に「N/A は合格ではない」と一覧を足す（ADR-045）。"""
    items = [
        (r["id"], m["metric"]) for r in table for m in r["key_metrics"] if m.get("status") == "na"
    ]
    if not items:
        return []
    listed = (
        ", ".join(f"{i}.{m}" for i, m in items)
        if lang == "en"
        else "、".join(f"{i}.{m}" for i, m in items)
    )
    if lang == "en":
        text = (
            "N/A = a criterion whose applicability condition (applies_if) is false (insufficient power). "
            f"It is not a pass and is excluded from the verdict: {listed}."
        )
    else:
        text = (
            "N/A = 適用条件（applies_if）が偽の基準（検出力不足）。合格ではなく、判定から外している: "
            f"{listed}。"
        )
    return ["", f"*{text}*"]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)
