"""軌跡メトリクス（原典 4.2）。すべて `Run` だけから決定的に計算する。

LLM を呼ぶ指標は `ablation.py` と `step_judge.py` に隔離する。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agenteval.core.normalize import action_key
from agenteval.core.schema import Run
from agenteval.env.tools import WRITE_TOOLS

READ_TOOLS = {
    "calendar_search",
    "mail_search",
    "contacts_search",
    "file_read",
    "external_lookup",
    "checks_run",
}
VERIFICATION_TOOLS = {"checks_run"}


@dataclass
class RunMetrics:
    """1 run のメトリクス。未定義は NaN（読むだけのタスクの検証行動率など）。"""

    run_id: str
    task_id: str
    version_id: str
    steps: int
    calls: int
    detour_ratio: float
    detour_excess: float
    dup_rate: float
    backtracks: int
    context_growth: int
    verification_rate: float
    recovery_rate: float
    stop_appropriate: bool
    premature_stop: bool
    overrun: bool
    finish_called: bool
    question_appropriate: bool
    tokens: int
    l_min_source: str
    passed: bool
    milestone_score: float
    violations: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def compute(run: Run, l_min: int | None = None, l_min_source: str = "task") -> RunMetrics:
    """1 run のメトリクスを出す。"""
    calls = [(c.name, c.args_norm) for s in run.steps for c in s.tool_calls]
    n_calls = len(calls)
    keys = [action_key(name, args) for name, args in calls]
    counts = Counter(keys)
    duplicates = sum(v - 1 for v in counts.values() if v > 1)
    steps = run.n_steps
    # 原典 4.2: 迂回率 D = L_actual / L_min（1 以上の比）。ADR-017。
    # `l_min` が無い run は NaN にして平均から除く（0 で割らない）。
    if l_min is None or l_min <= 0 or steps == 0:
        detour_ratio = math.nan
        detour_excess = math.nan
    else:
        detour_ratio = round(steps / l_min, 4)
        detour_excess = round(max(0.0, (steps - l_min) / steps), 4)
    stop = _stop_quality(run)
    return RunMetrics(
        run_id=run.run_id,
        task_id=run.task_id,
        version_id=run.version_id,
        steps=steps,
        calls=n_calls,
        detour_ratio=detour_ratio,
        detour_excess=detour_excess,
        dup_rate=round(duplicates / n_calls, 4) if n_calls else 0.0,
        backtracks=_backtracks(run),
        context_growth=_context_growth(run),
        verification_rate=_verification_rate(run),
        recovery_rate=_recovery_rate(run),
        stop_appropriate=stop["appropriate"],
        premature_stop=stop["premature"],
        overrun=stop["overrun"],
        finish_called=_finish_called(run),
        question_appropriate=_question_appropriate(run),
        tokens=run.cost.total_tokens,
        l_min_source=l_min_source,
        passed=run.passed(),
        milestone_score=run.outcome.milestone_score if run.outcome else 0.0,
        violations=len(run.outcome.linter_violations) if run.outcome else 0,
    )


def _backtracks(run: Run) -> int:
    """後戻り = 一度書いた対象をもう一度読み直す、または同じ読み取りを繰り返す回数。"""
    seen_reads: set[str] = set()
    written: set[str] = set()
    count = 0
    for step in run.steps:
        for call in step.tool_calls:
            key = action_key(call.name, call.args_norm)
            target = str(call.args_norm.get("path") or call.args_norm.get("event_id") or "")
            if call.name in READ_TOOLS:
                if key in seen_reads or (target and target in written):
                    count += 1
                seen_reads.add(key)
            if call.name in WRITE_TOOLS and target:
                written.add(target)
    return count


def _context_growth(run: Run) -> int:
    if not run.steps:
        return 0
    return run.steps[-1].context_tokens - run.steps[0].context_tokens


def _verification_rate(run: Run) -> float:
    """分母は「書込み系ツールを 1 回以上呼んだ run」。読むだけなら NaN。"""
    wrote = any(c.name in WRITE_TOOLS for s in run.steps for c in s.tool_calls)
    if not wrote:
        return math.nan
    verified = any(c.name in VERIFICATION_TOOLS for s in run.steps for c in s.tool_calls)
    return 1.0 if verified else 0.0


def _recovery_rate(run: Run) -> float:
    """エラーの後に同じツールが成功した割合。エラーが無ければ NaN。"""
    errors = 0
    recovered = 0
    pending: dict[str, bool] = {}
    for step in run.steps:
        results = {r.id: r for r in step.tool_results}
        for call in step.tool_calls:
            result = results.get(call.id)
            if result is None:
                continue
            if result.is_error:
                errors += 1
                pending[call.name] = True
            elif pending.pop(call.name, False):
                recovered += 1
    if errors == 0:
        return math.nan
    return round(recovered / errors, 4)


def _finish_called(run: Run) -> bool:
    """最後のツール呼び出しが `finish` か（改訂前の `stop_appropriate`）。

    live では実エージェントの 8 割がここに当たらない（IMPROVEMENT.md L1）。
    「ツール呼び出しの作法」を測る量であって停止の適切さではないので、名前を分けた。
    """
    names = run.tool_names()
    return bool(names) and names[-1] == "finish"


def _stop_quality(run: Run) -> dict[str, bool]:
    """停止適切性（原典 4.2、ADR-019）。

    - `premature` 未達なのに完了を宣言した（マイルストーン未到達で終了）
    - `overrun` 全マイルストーン到達後もステップを続けた
    - `appropriate` どちらでもない
    `milestone_steps` が空の run（マイルストーン未定義）は判定できないので両方 False。
    """
    outcome = run.outcome
    if outcome is None or not outcome.milestone_steps:
        return {"appropriate": True, "premature": False, "overrun": False}
    steps = outcome.milestone_steps
    unreached = [k for k, v in steps.items() if v < 0]
    premature = bool(unreached)
    overrun = False
    if not unreached and steps:
        last_needed = max(steps.values())
        # 最終ステップが「全マイルストーン到達 ＋ finish」より後ろなら継続しすぎ。
        # finish は 1 ステップぶん許容する。
        overrun = (run.n_steps - 1) > last_needed + 1
    return {
        "appropriate": not premature and not overrun,
        "premature": premature,
        "overrun": overrun,
    }


def _question_appropriate(run: Run) -> bool:
    """kind に応じた質問適切性。タスク種別は task 側で判定する（ここでは形だけ見る）。"""
    asked = "?" in run.final_text or "？" in run.final_text
    wrote = any(c.name in WRITE_TOOLS for s in run.steps for c in s.tool_calls)
    return asked and not wrote


def question_appropriate_for(run: Run, kind: str) -> bool:
    """`ambiguous` は「質問して止まる」、`normal` は「質問せず完了する」が適切。"""
    asked = _question_appropriate(run)
    if kind == "ambiguous":
        return asked
    return (not asked) and _finish_called(run)


def aggregate(metrics: list[RunMetrics], field_name: str) -> float:
    """NaN を除いた平均。全部 NaN なら NaN。"""
    values = [getattr(m, field_name) for m in metrics]
    numeric = [
        float(v)
        for v in values
        if isinstance(v, (int, float, bool)) and not (isinstance(v, float) and math.isnan(v))
    ]
    if not numeric:
        return math.nan
    return sum(numeric) / len(numeric)


def by_version(metrics: list[RunMetrics], field_name: str) -> dict[str, float]:
    """版ごとの平均。"""
    groups: dict[str, list[RunMetrics]] = {}
    for m in metrics:
        groups.setdefault(m.version_id, []).append(m)
    return {v: aggregate(items, field_name) for v, items in sorted(groups.items())}
