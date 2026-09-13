"""結果ページの生成（`docs/results/EX-Y.md`）。

5 項目はレジストリ（= カタログ）から写す。方法・気づきは実験スクリプトが書いた文章を使う。
数値には必ず来歴ラベルを付ける。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.llm.cost import REPO_ROOT

RESULTS_DIR = REPO_ROOT / "docs" / "results"

TEMPLATE = """# {id} {title}

## 5項目
- 仮説: {hypothesis}
- 植込み条件: {planted}
- 対照: {control}
- 合格基準: {criteria}
- 来歴: {provenance}

## 方法
{method}

## 結果
{table}
{figures}

## 判定
{verdict}

{verdict_reason}

## 気づき・限界
{notes}

---
生成: `uv run agenteval exp {script_id}` / 実行時刻 {ran_at} / コスト {usd} USD
"""


def format_value(value: Any) -> str:
    """数値と来歴ラベルを 1 セルにする。"""
    if isinstance(value, dict) and "value" in value:
        raw = value["value"]
        text = (
            f"{raw:.4g}"
            if isinstance(raw, (int, float)) and not isinstance(raw, bool)
            else str(raw)
        )
        return f"{text} [{value.get('provenance', '?')}]"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def metrics_table(metrics: dict[str, Any], criteria: list[dict[str, Any]]) -> str:
    """指標の表。合格基準に対応する行には基準と可否を書く。"""
    by_metric = {c["metric"]: c for c in criteria}
    lines = ["| 指標 | 値（来歴） | 基準 | 判定 |", "|---|---|---|---|"]
    for key in sorted(metrics):
        value = metrics[key]
        criterion = by_metric.get(key)
        if criterion is None:
            lines.append(f"| {key} | {format_value(value)} | — | — |")
            continue
        ok = check_criterion(value, criterion)
        lines.append(
            f"| {key} | {format_value(value)} | {criterion['op']} {criterion['value']} | {'○' if ok else '×'} |"
        )
    return "\n".join(lines)


def raw_value(value: Any) -> Any:
    return value["value"] if isinstance(value, dict) and "value" in value else value


def check_criterion(value: Any, criterion: dict[str, Any]) -> bool:
    """1 つの合格基準を評価する。"""
    actual = raw_value(value)
    target = criterion["value"]
    op = criterion["op"]
    if actual is None:
        return False
    if op == ">=":
        return bool(actual >= target)
    if op == "<=":
        return bool(actual <= target)
    if op == ">":
        return bool(actual > target)
    if op == "<":
        return bool(actual < target)
    if op == "==":
        return bool(actual == target)
    raise ValueError(f"未知の演算子: {op}")


def render(meta: dict[str, Any], result: dict[str, Any]) -> str:
    """結果ページの markdown を作る。"""
    criteria = meta.get("criteria", [])
    figures = "\n".join(f"![{name}]({path})" for name, path in (result.get("figures") or []))
    notes = "\n".join(f"- {n}" for n in (result.get("notes") or []))
    return TEMPLATE.format(
        id=meta["id"],
        title=meta["title"],
        hypothesis=meta.get("hypothesis", ""),
        planted=meta.get("planted_text", meta.get("planted", "")),
        control=meta.get("control_text", meta.get("control", "")),
        criteria="、".join(f"{c['metric']} {c['op']} {c['value']}" for c in criteria),
        provenance=meta.get("provenance", ""),
        method=result.get("method", ""),
        table=metrics_table(result.get("metrics", {}), criteria),
        figures=figures,
        verdict=result.get("verdict", "PENDING"),
        verdict_reason=result.get("verdict_reason", ""),
        notes=notes,
        script_id=meta["id"].lower().replace("-", "_"),
        ran_at=result.get("ran_at", "-"),
        usd=result.get("usd", 0.0),
    )


def write(meta: dict[str, Any], result: dict[str, Any], directory: Path | None = None) -> Path:
    """結果ページを書き出す。"""
    target = (directory or RESULTS_DIR) / f"{meta['id']}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(meta, result), encoding="utf-8")
    return target
