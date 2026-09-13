"""CLI（typer）。`agenteval <command>`。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

if TYPE_CHECKING:
    from agenteval.llm.client import Mode

import typer

app = typer.Typer(add_completion=False, help="評価手法の例示的実装検証ツール")


@app.command()
def run(
    version: Annotated[str, typer.Option("--version", help="版 ID")],
    task: Annotated[str, typer.Option("--task", help="タスク ID")],
    mode: Annotated[str, typer.Option("--mode", help="sim | replay | auto | record")] = "sim",
    seed: Annotated[int, typer.Option("--seed")] = 20260913,
    repeat: Annotated[int, typer.Option("--repeat")] = 0,
    save: Annotated[bool, typer.Option("--save/--no-save")] = True,
) -> None:
    """1 run を実行して結果を表示する。"""
    from agenteval.agent.loop import RunOptions, run_task
    from agenteval.core.registry import get_task, get_version
    from agenteval.core.store import save_run, trace_hash

    options = RunOptions(seed=seed, repeat=repeat)
    options.mode = cast("Mode", mode)
    result = run_task(get_task(task), get_version(version), options)
    if save:
        save_run(result)
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "passed": result.passed(),
                "steps": result.n_steps,
                "milestone_score": round(result.outcome.milestone_score, 3)
                if result.outcome
                else None,
                "violations": [
                    v.rule_id for v in (result.outcome.linter_violations if result.outcome else [])
                ],
                "tools": result.tool_names(),
                "trace_hash": trace_hash(result)[:16],
                "provenance": result.provenance,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def cost() -> None:
    """累積コストと残予算、cache hit 率。"""
    import os

    from agenteval.llm.cost import ledger_summary

    summary = ledger_summary()
    budget = os.environ.get("AGENTEVAL_BUDGET_USD")
    summary["budget_usd"] = float(budget) if budget else None
    summary["remaining_usd"] = (float(budget) - summary["usd"]) if budget else None
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


corpus_app = typer.Typer(help="コーパスの生成")
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("build")
def corpus_build(
    plan: Annotated[Path, typer.Option("--plan")] = Path("corpus.yaml"),
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run")] = True,
    live: Annotated[bool, typer.Option("--live/--no-live")] = False,
    sim: Annotated[bool, typer.Option("--sim/--no-sim")] = False,
) -> None:
    """版 × タスク × 反復のコーパスを作る。既定は見積りのみ。"""
    from agenteval.reports.corpus import build_corpus, estimate

    if dry_run and not (live or sim):
        typer.echo(json.dumps(estimate(plan), ensure_ascii=False, indent=2))
        return
    mode: Mode = "auto" if live else "sim"
    summary = build_corpus(plan, mode=mode)
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command()
def exp(
    experiment_id: Annotated[str, typer.Argument(help="実験 ID（e3_1 / E3-1）")],
    live: Annotated[bool, typer.Option("--live/--no-live")] = False,
    seed: Annotated[int, typer.Option("--seed")] = 20260913,
) -> None:
    """実験を 1 つ実行し、結果 JSON と結果ページを更新する。"""
    from agenteval.reports.runner import run_experiment

    result = run_experiment(experiment_id, live=live, seed=seed)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command()
def verify() -> None:
    """全実験の合格基準を照合し docs/results/summary.md を生成する。"""
    from agenteval.reports.verify import verify_all

    table = verify_all()
    typer.echo(json.dumps(table, ensure_ascii=False, indent=2))


@app.command("lifecycle")
def lifecycle_tick() -> None:
    """メトリクスからテストの状態を遷移させる（8.10）。"""
    from agenteval.drift.lifecycle import tick_all

    typer.echo(json.dumps(tick_all(), ensure_ascii=False, indent=2))


@app.command("prod2test")
def prod2test_review(
    auto_approve: Annotated[bool, typer.Option("--auto-approve/--interactive")] = True,
) -> None:
    """本番 run からテストを起草して登録する（8.2）。"""
    from agenteval.drift.prod2test import review

    typer.echo(json.dumps(review(auto_approve=auto_approve), ensure_ascii=False, indent=2))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
