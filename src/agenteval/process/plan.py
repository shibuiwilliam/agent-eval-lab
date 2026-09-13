"""計画の外在化・DAG 検査・乖離率 δ（原典 4.7、ADR-005）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from agenteval.core.schema import Run
from agenteval.env.tools import TOOL_NAMES


@dataclass
class PlanCheck:
    """計画の実行前検査の結果。"""

    acyclic: bool
    refs_valid: bool
    tools_valid: bool
    issues: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.acyclic and self.refs_valid and self.tools_valid


def check_plan(plan: list[dict[str, Any]] | None) -> PlanCheck:
    """DAG 非循環・`depends_on` の参照妥当性・ツール名の存在を検査する。"""
    if not plan:
        return PlanCheck(False, False, False, ["計画が提出されていない"])
    issues: list[str] = []
    ids = {str(step.get("id")) for step in plan}
    graph = nx.DiGraph()
    refs_valid = True
    tools_valid = True
    for step in plan:
        sid = str(step.get("id"))
        graph.add_node(sid)
        for dep in step.get("depends_on") or []:
            if str(dep) not in ids:
                refs_valid = False
                issues.append(f"存在しない依存: {sid} -> {dep}")
            graph.add_edge(str(dep), sid)
        tool = str(step.get("tool"))
        if tool not in TOOL_NAMES:
            tools_valid = False
            issues.append(f"存在しないツール: {tool}")
    acyclic = nx.is_directed_acyclic_graph(graph)
    if not acyclic:
        issues.append("計画が巡回している")
    return PlanCheck(acyclic, refs_valid, tools_valid, issues)


def edit_distance(a: list[str], b: list[str]) -> int:
    """列の編集距離。"""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[n][m]


def deviation(run: Run) -> float:
    """乖離率 δ = 計画のツール名列と実行のツール名列の編集距離 ÷ 長い方（ADR-005）。"""
    planned = [str(s.get("tool")) for s in (run.plan or [])]
    executed = [t for t in run.tool_names() if t != "submit_plan"]
    if not planned:
        return 1.0
    return round(edit_distance(planned, executed) / max(len(planned), len(executed), 1), 4)


def deviation_sites(run: Run) -> dict[str, Any]:
    """乖離箇所だけを抽出する（ジャッジに全体を読ませないため）。"""
    planned = [str(s.get("tool")) for s in (run.plan or [])]
    executed = [t for t in run.tool_names() if t != "submit_plan"]
    skipped = [t for t in planned if t not in executed]
    added = [t for t in executed if t not in planned]
    return {
        "planned": planned,
        "executed": executed,
        "skipped_tools": skipped,
        "added_tools": added,
        "environment_forced": any(r.is_error for s in run.steps for r in s.tool_results),
    }


def judge_deviation(run: Run, client: Any | None = None) -> Any:
    """乖離の正当性をジャッジに問う（乖離箇所だけを渡す）。"""
    from agenteval.judge.rubric import DEVIATION_RUBRIC, offline_deviation_judge
    from agenteval.judge.verdict import ask

    payload = json.dumps(deviation_sites(run), ensure_ascii=False)
    return ask(client, DEVIATION_RUBRIC, payload, offline_deviation_judge)
