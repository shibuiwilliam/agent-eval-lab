"""レジストリと結果 JSON を突き合わせて PASS / FAIL / NEGATIVE / PENDING を出す。

`criteria` に無い指標で合格を主張しない。来歴ラベルの無い数値は拒否する（ADR-004）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agenteval.llm.cost import REPO_ROOT
from agenteval.reports.pages import check_criterion, raw_value

REGISTRY_PATH = REPO_ROOT / "experiments" / "registry.yaml"
RESULT_DIR = REPO_ROOT / "data" / "results"
SUMMARY_PATH = REPO_ROOT / "docs" / "results" / "summary.md"


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
            }
        )
    if not rows:
        return {"verdict": "PENDING", "detail": "criteria が空", "criteria": rows}
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
    return table


def write_summary(table: list[dict[str, Any]], path: Path | None = None) -> Path:
    """`docs/results/summary.md` を生成する（手で編集しない）。"""
    target = path or SUMMARY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for row in table:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    lines = [
        "# 検証サマリ",
        "",
        "このファイルは `uv run agenteval verify` の生成物。手で編集しない。",
        "",
        "| 判定 | 件数 |",
        "|---|---|",
    ]
    for verdict in ("PASS", "NEGATIVE", "FAIL", "PENDING"):
        lines.append(f"| {verdict} | {counts.get(verdict, 0)} |")
    lines += [
        "",
        "| 実験 | 章 | タイトル | 判定 | 主要指標 | 来歴 | コスト(USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        metrics = " / ".join(
            f"{m['metric']}={_fmt(m['actual'])}{'○' if m['ok'] else '×'}"
            for m in row["key_metrics"]
        )
        lines.append(
            f"| [{row['id']}]({row['id']}.md) | {row['chapter']} | {row['title']} | {row['verdict']} "
            f"| {metrics} | {row['provenance']} | {row['usd']} |"
        )
    lines += ["", f"総コスト: {round(sum(r['usd'] for r in table), 4)} USD", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)
