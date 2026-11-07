"""レジストリと結果 JSON を突き合わせて PASS / FAIL / NEGATIVE / PENDING を出す。

`criteria` に無い指標で合格を主張しない。来歴ラベルの無い数値は拒否する（ADR-004）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agenteval.llm.cost import REPO_ROOT
from agenteval.reports import i18n
from agenteval.reports.pages import check_criterion, raw_value

REGISTRY_PATH = REPO_ROOT / "experiments" / "registry.yaml"
RESULT_DIR = REPO_ROOT / "data" / "results"
SUMMARY_PATH = REPO_ROOT / "docs" / "results" / "summary.md"
SUMMARY_PATH_EN = REPO_ROOT / "docs" / "results" / "summary.en.md"


def load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    """registry.yaml を読む。"""
    data = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data if isinstance(data, list) else data.get("experiments", [])
    return entries


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
    for criterion in criteria:
        value = (result.get("metrics") or {}).get(criterion["metric"])
        ok = value is not None and check_criterion(value, criterion)
        rows.append(
            {
                "metric": criterion["metric"],
                "op": criterion["op"],
                "target": criterion["value"],
                "actual": raw_value(value),
                "ok": ok,
                "provisional": bool(criterion.get("provisional")),
            }
        )
    if not rows:
        return {"verdict": "PENDING", "detail": "criteria が空", "criteria": rows}
    forced = result.get("forced_failure")
    if forced:
        # 章 C の実験が、基準の指標では表せない欠陥（例: EC-R2 のカセットの塩の不備）を見つけたとき。基準の達成より優先する
        return {"verdict": "FAIL", "detail": f"強制 FAIL: {forced}", "criteria": rows}
    if all(r["ok"] for r in rows):
        return {"verdict": "PASS", "detail": "全基準を満たした", "criteria": rows}
    declared = str(result.get("failure_type", "fail")).lower()
    verdict = "NEGATIVE" if declared == "negative" else "FAIL"
    failed = [r["metric"] for r in rows if not r["ok"]]
    return {"verdict": verdict, "detail": f"未達: {failed}", "criteria": rows}


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
                "provenance": (result or {}).get("provenance", entry.get("provenance", "")),
                "usd": (result or {}).get("usd", 0.0),
                "key_metrics": judged["criteria"],
            }
        )
    write_summary(table)
    write_summary_en(table)
    return table


def write_summary(table: list[dict[str, Any]], path: Path | None = None) -> Path:
    """`docs/results/summary.md` を生成する（手で編集しない）。"""
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
    lines += ["", f"総コスト: {round(sum(r['usd'] for r in table), 4)} USD", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def write_summary_en(table: list[dict[str, Any]], path: Path | None = None) -> Path:
    """`docs/results/summary.en.md` を生成する。タイトルだけ registry.en.yaml から取る。"""
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
    lines += ["", f"Total cost: {round(sum(r['usd'] for r in table), 4)} USD", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# 章 C（コーディングエージェント。NEW_IDEA 第 1 段）の基準のうち暫定のもの（W3-A2 がパイロットから確定する。ADR-033）
CHAPTER_C_PROVISIONAL = ("EC-B2", "EC-R1", "EC-R2")


def _chapter_c_footnote(table: list[dict[str, Any]], lang: str) -> list[str]:
    """章 C の行があるときだけ、表の下に注記を足す（既存の章の行は変えない）。

    章 C は第 1 段の 6 実験。結果 JSON が無い間は PENDING で現れる。暫定の基準を持つ実験を明示する。
    """
    chapter_c = [r["id"] for r in table if str(r["chapter"]) == "C"]
    if not chapter_c:
        return []
    provisional = [i for i in chapter_c if i in CHAPTER_C_PROVISIONAL]
    if lang == "en":
        text = (
            f"Chapter C = coding agent (NEW_IDEA stage 1, {len(chapter_c)} experiments). "
            "Criteria of the live experiments are provisional until the pilot fixes them (ADR-033): "
            f"{', '.join(provisional)}. A row stays PENDING until its result JSON exists."
        )
    else:
        text = (
            f"章 C = コーディングエージェント（NEW_IDEA 第 1 段、{len(chapter_c)} 実験）。"
            f"暫定の基準を持つ実験（パイロットで確定する。ADR-033）: {'、'.join(provisional)}。"
            "結果 JSON が無い行は PENDING のまま。"
        )
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
        metrics = " / ".join(
            f"{m['metric']}={_fmt(m['actual'])}{'○' if m['ok'] else '×'}{mark if m.get('provisional') else ''}"
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


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)
