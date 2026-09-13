"""コーパス生成（版 × タスク × 反復）と見積り。"""

from __future__ import annotations

from concurrent import futures
from pathlib import Path
from typing import Any

import yaml

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version, load_tasks, load_versions
from agenteval.core.schema import Run
from agenteval.core.store import TRACE_DIR, save_run
from agenteval.llm.client import Mode
from agenteval.llm.cost import REPO_ROOT, load_pricing


def load_plan(path: Path | None = None) -> dict[str, Any]:
    """corpus.yaml を読む。"""
    target = path or (REPO_ROOT / "corpus.yaml")
    data: dict[str, Any] = yaml.safe_load(target.read_text(encoding="utf-8"))
    return data


def plan_units(plan: dict[str, Any]) -> list[tuple[str, str, int]]:
    """(task_id, version_id, repeat) の全組合せ。"""
    tasks = sorted(load_tasks())
    repeats = plan.get("repeats", {})
    default = int(repeats.get("default", 3))
    units: list[tuple[str, str, int]] = []
    for version_id in plan["versions"]:
        n = int(repeats.get(version_id, default))
        for task_id in tasks:
            units.extend((task_id, version_id, r) for r in range(n))
    return units


def estimate(path: Path | None = None) -> dict[str, Any]:
    """--dry-run の見積り。過去 run が無ければ保守的に 1.5 倍する。"""
    plan = load_plan(path)
    units = plan_units(plan)
    defaults = plan.get("estimate_defaults", {})
    steps = float(defaults.get("steps_per_run", 7))
    in_tok = float(defaults.get("input_tokens_per_call", 4000))
    out_tok = float(defaults.get("output_tokens_per_call", 200))
    hit = float(defaults.get("cache_hit_ratio", 0.5))
    pricing = load_pricing()
    have_history = any(TRACE_DIR.glob("*.json"))
    factor = 1.0 if have_history else 1.5
    by_version: dict[str, dict[str, Any]] = {}
    total_calls = 0
    total_usd = 0.0
    for version_id in plan["versions"]:
        n_units = len([u for u in units if u[1] == version_id])
        calls = int(n_units * steps)
        model = (
            get_version(version_id).model_id()
            if version_id in load_versions()
            else "claude-haiku-4-5-20251001"
        )
        price = pricing.models[model]
        usd = (
            calls
            * (
                in_tok * (1 - hit) * price["input"]
                + in_tok * hit * price["cache_read"]
                + out_tok * price["output"]
            )
            / 1_000_000
        ) * factor
        by_version[version_id] = {
            "runs": n_units,
            "calls": calls,
            "usd": round(usd, 3),
            "model": model,
        }
        total_calls += calls
        total_usd += usd
    return {
        "units": len(units),
        "calls": total_calls,
        "usd_estimate": round(total_usd, 2),
        "conservative_factor": factor,
        "by_version": by_version,
        "note": "sim モードでは API を呼ばないためコストは 0。この見積りは live 実行した場合の値",
    }


def build_corpus(
    path: Path | None = None,
    mode: Mode = "sim",
    directory: Path | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """コーパスを生成して保存する。"""
    plan = load_plan(path)
    units = plan_units(plan)
    workers = concurrency or int(plan.get("concurrency", 4))
    seed = int(plan.get("seed", 20260913))

    def one(unit: tuple[str, str, int]) -> Run:
        task_id, version_id, repeat = unit
        run = run_task(
            get_task(task_id),
            get_version(version_id),
            RunOptions(mode=mode, seed=seed, repeat=repeat, keep_snapshots=repeat == 0),
        )
        save_run(run, directory)
        return run

    results: list[Run] = []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, units))

    by_version: dict[str, dict[str, Any]] = {}
    for run in results:
        entry = by_version.setdefault(run.version_id, {"runs": 0, "passed": 0, "steps": 0})
        entry["runs"] += 1
        entry["passed"] += int(run.passed())
        entry["steps"] += run.n_steps
    for entry in by_version.values():
        entry["pass_rate"] = round(entry["passed"] / entry["runs"], 3)
        entry["mean_steps"] = round(entry["steps"] / entry["runs"], 2)
    return {
        "units": len(units),
        "mode": mode,
        "usd": round(sum(r.cost.usd for r in results), 4),
        "by_version": by_version,
    }
