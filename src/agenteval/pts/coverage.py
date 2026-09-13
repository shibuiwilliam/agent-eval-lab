"""軌跡カバレッジ依存グラフ（原典 3.2）。

`U(t)` = そのテストが実行時に触れた要素の集合:
  - 呼んだツール名
  - 参照したプロンプト section（下の対応表による近似。ADR に相当する近似であることを明記する）
  - 使ったフィクスチャ要素（触れた資源の id / パス）
"""

from __future__ import annotations

from collections import defaultdict

from agenteval.core.schema import Change, Run

# section id → その section が支配するツール。プロンプト section の「参照」を
# 軌跡から推定するための対応表（原典 3.2 のカバレッジを近似する）。
SECTION_TOOLS: dict[str, set[str]] = {
    "role": set(),
    "workflow": {"calendar_search", "mail_search", "file_read", "external_lookup"},
    "date_format": {"calendar_search", "calendar_create"},
    "verification": {"checks_run"},
    "safety": {"file_backup", "file_delete", "calendar_delete"},
    "scope": {"file_write", "file_delete"},
    "clarify": {"finish"},
    "finish_rules": {"finish"},
    "efficiency": set(),
    "thoroughness": {"calendar_search", "mail_search", "file_read"},
    "plan_override": {"submit_plan"},
    "examples": set(),
    "external_fields": {"external_lookup"},
}


def units(run: Run) -> set[str]:
    """1 run が触れた要素の集合 `U(t)`。"""
    out: set[str] = set()
    tools = {c.name for s in run.steps for c in s.tool_calls}
    out |= {f"tool:{name}" for name in tools}
    for section, governed in SECTION_TOOLS.items():
        if governed & tools:
            out.add(f"section:{section}")
    for step in run.steps:
        for call in step.tool_calls:
            for key in ("path", "event_id", "key", "to"):
                value = call.args_norm.get(key)
                if value:
                    out.add(f"resource:{value}")
    return out


def coverage_map(runs: list[Run]) -> dict[str, set[str]]:
    """タスク ID → 触れた要素（同一タスクの複数 run を和で集める）。"""
    out: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        out[run.task_id] |= units(run)
    return dict(out)


def change_units(change: Change) -> set[str]:
    """変更が触る要素 `C(Δ)`。"""
    prefix = {"prompt": "section", "tool": "tool", "fixture": "resource"}.get(change.kind)
    if prefix is None:
        # model / config / code は全体に効くので、要素で絞らない
        return set()
    return {f"{prefix}:{name}" for name in change.components}


def candidates(change: Change, coverage: dict[str, set[str]]) -> set[str]:
    """候補集合 `T_cand(Δ)`。要素で絞れない変更（model / config）は全件。"""
    target = change_units(change)
    if not target:
        return set(coverage)
    return {task_id for task_id, used in coverage.items() if used & target}


def candidate_ratio(change: Change, coverage: dict[str, set[str]]) -> float:
    """候補の割合。"""
    if not coverage:
        return 0.0
    return round(len(candidates(change, coverage)) / len(coverage), 4)
