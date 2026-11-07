"""CLI（typer）。`agenteval <command>`。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

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
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="実行せず、依存する入力（段の run の有無）と費用の見込みを出す。API は呼ばない",
        ),
    ] = False,
    failure_type: Annotated[
        str,
        typer.Option(
            "--failure-type",
            help="章 C の実験で基準が未達のときの判定の種類: fail（既定）| negative。negative は --reason が必須",
        ),
    ] = "",
    reason: Annotated[
        str,
        typer.Option(
            "--reason", help="--failure-type negative の理由（結果 JSON と結果ページに残る）"
        ),
    ] = "",
) -> None:
    """実験を 1 つ実行し、結果 JSON と結果ページを更新する。

    章 C の自動の判定は未達なら FAIL にする。NEGATIVE（実装は正しいが手法が主張どおり働かなかった）への切り替えは、
    designer が検出力（MDE）と植込みの効果（操作確認）を確かめたうえで `--failure-type negative --reason "<理由>"` で明示する。
    """
    import os

    if failure_type not in ("", "fail", "negative"):
        raise typer.BadParameter("--failure-type は fail | negative")
    if failure_type == "negative" and not reason.strip():
        raise typer.BadParameter("--failure-type negative には --reason が要る")
    if reason and failure_type != "negative":
        raise typer.BadParameter("--reason は --failure-type negative のときだけ指定できる")
    if failure_type and dry_run:
        raise typer.BadParameter("--failure-type は --dry-run と併用できない")
    if failure_type:
        from agenteval.reports.runner import normalize_id
        from agenteval.reports.verify import load_registry

        entry = next(
            (
                e
                for e in load_registry()
                if normalize_id(str(e["id"])) == normalize_id(experiment_id)
            ),
            None,
        )
        if entry is None or str(entry.get("chapter")) != "C":
            raise typer.BadParameter("--failure-type は章 C の実験だけに指定できる")
    if live and dry_run:
        raise typer.BadParameter("--live と --dry-run は同時に指定できない")
    if dry_run:
        from agenteval.reports.coding_corpus import experiment_dry_run

        typer.echo(
            json.dumps(
                experiment_dry_run(experiment_id, seed=seed),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return

    from agenteval.reports.coding_corpus import StageIncompleteError
    from agenteval.reports.runner import run_experiment

    if failure_type:
        # 実験スクリプトの main(live, seed) の形を変えないため、環境変数で渡す（章 C の finalize_coding が読む）
        os.environ["AGENTEVAL_FAILURE_TYPE"] = failure_type
        os.environ["AGENTEVAL_FAILURE_REASON"] = reason.strip()
    try:
        result = run_experiment(experiment_id, live=live, seed=seed)
    except StageIncompleteError as exc:
        # 章 C の live の実験は run を作らない。結果 JSON を書かずに案内して止まる（verify では PENDING のまま）
        typer.echo(f"[停止] {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        os.environ.pop("AGENTEVAL_FAILURE_TYPE", None)
        os.environ.pop("AGENTEVAL_FAILURE_REASON", None)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


coding_app = typer.Typer(help="コーディング環境（NEW_IDEA 第 1 段）。run・コーパス・集計")
app.add_typer(coding_app, name="coding")

CODING_PLAN_DEFAULT = Path("coding/corpus.yaml")


@coding_app.command("run")
def coding_run(
    version: Annotated[
        str, typer.Option("--version", help="コーディングの版 ID（c01_baseline など）")
    ],
    task: Annotated[str, typer.Option("--task", help="コーディングのタスク ID（CT-...）")],
    mode: Annotated[
        str, typer.Option("--mode", help="sim | record | replay（auto は拒否される）")
    ] = "sim",
    stage: Annotated[str, typer.Option("--stage", help="段（run_id に入る）")] = "unit",
    repeat: Annotated[int, typer.Option("--repeat")] = 0,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="既定: coding/corpus.yaml の段の seed（無い段は 0）"),
    ] = None,
    script: Annotated[
        str,
        typer.Option("--script", help="sim の台本: gold | spec（record・replay では指定しない）"),
    ] = "",
    plan: Annotated[Path, typer.Option("--plan")] = CODING_PLAN_DEFAULT,
    approved: Annotated[
        str,
        typer.Option(
            "--approved",
            help="record・replay で live の段（pilot・opcheck<N>・corpus）を指定するとき必須。pilot・opcheck<N> は G1、corpus は G2",
        ),
    ] = "",
) -> None:
    """1 run を実行して結果を表示する。live は record だけ（ANTHROPIC_API_KEY と AGENTEVAL_BUDGET_USD が要る）。"""
    from agenteval.agent.coding_loop import CodingRunOptions, run_coding_task
    from agenteval.agent.scripted import gold_script, specialization_script
    from agenteval.core.coding_registry import get_coding_task, get_coding_version
    from agenteval.econ.outcome import classify
    from agenteval.reports.coding_corpus import (
        PlanError,
        check_gate,
        load_plan,
        required_gate,
        stage_seed,
    )

    if mode not in ("sim", "record", "replay"):
        raise typer.BadParameter("--mode は sim | record | replay（auto は拒否される）")
    if mode == "record" and required_gate(stage) is None:
        # record は live API を呼ぶ。ゲートに対応しない段（unit・未知の名前）では承認なしに走ってしまうので拒否する
        raise typer.BadParameter(
            f"--mode record は live の段（pilot・opcheck<N>・corpus）だけ。段 {stage!r} はゲートに対応しない"
        )
    if mode in ("record", "replay"):
        # live の段をコーパスのゲートの外から始められないようにする（1 run でも段の run_id と seed が使われるため）
        try:
            check_gate(stage, approved)
        except PlanError as exc:
            raise typer.BadParameter(str(exc)) from exc
    if seed is None:
        try:
            seed = stage_seed(load_plan(plan), stage)
        except (PlanError, KeyError):
            seed = 0
    task_def = get_coding_task(task)
    script_obj = None
    if mode == "sim":
        if script not in ("gold", "spec"):
            raise typer.BadParameter("--mode sim には --script gold|spec が要る")
        script_obj = gold_script(task_def) if script == "gold" else specialization_script(task_def)
    elif script:
        raise typer.BadParameter("--script は --mode sim のときだけ指定できる")
    run_result, outcome = run_coding_task(
        task_def,
        get_coding_version(version),
        CodingRunOptions(
            mode=cast("Mode", mode), stage=stage, repeat=repeat, seed=seed, script=script_obj
        ),
    )
    typer.echo(
        json.dumps(
            {
                "run_id": run_result.run_id,
                "mode": run_result.mode,
                "provenance": run_result.provenance,
                "class": classify(run_result) if task_def.kind == "bugfix" else None,
                "finished": outcome.finished,
                "visible_passed": outcome.visible.passed if outcome.visible else None,
                "hidden_passed": outcome.hidden.passed if outcome.hidden else None,
                "steps": run_result.n_steps,
                "usd": run_result.cost.usd,
                "test_files_modified": outcome.test_files_modified,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@coding_app.command("corpus")
def coding_corpus(
    plan: Annotated[Path, typer.Option("--plan")] = CODING_PLAN_DEFAULT,
    stage: Annotated[
        str, typer.Option("--stage", help="pilot | opcheck | opcheck<N> | corpus")
    ] = "pilot",
    dry_run: Annotated[
        bool | None,
        typer.Option("--dry-run/--no-dry-run", help="見積りだけ（既定。--live とは併用できない）"),
    ] = None,
    live: Annotated[bool, typer.Option("--live/--no-live")] = False,
    mode: Annotated[str, typer.Option("--mode", help="record（live。塩付き）| replay")] = "record",
    shard: Annotated[
        str, typer.Option("--shard", help="i/n（0 <= i < n）。位置 mod n で分ける")
    ] = "",
    approved: Annotated[
        str,
        typer.Option(
            "--approved",
            help="--live のとき必須。pilot・opcheck<N> は G1、corpus は G2（段と対応しなければ拒否）",
        ),
    ] = "",
) -> None:
    """段（pilot・opcheck・corpus）の見積りを出す。`--live` で未完了の run を実行する（承認が要る）。

    既定は見積りだけ（API を呼ばない）。`--live` は段に対応する `--approved`（pilot・opcheck は G1、corpus は G2）、
    ANTHROPIC_API_KEY、AGENTEVAL_BUDGET_USD（累積上限）が揃い、上限が保守見積り（×1.5）の残りを賄えるときだけ始まる。
    `--live` と `--dry-run` は併用できない。`auto` は使わない（コーディングのループが拒否する）。
    予算停止・途中終了（.partial）の run・未完了の run があれば、非 0 で終了する。
    """
    import os

    from agenteval.reports.coding_corpus import (
        PlanError,
        check_gate,
        estimate_stage,
        load_plan,
        parse_shard,
        run_stage,
    )

    if live and dry_run:
        raise typer.BadParameter("--live と --dry-run は同時に指定できない")
    if dry_run is False and not live:
        raise typer.BadParameter(
            "--no-dry-run だけでは何も実行しない（実行するには --live を付ける）"
        )
    if mode not in ("record", "replay"):
        raise typer.BadParameter("--mode は record | replay（auto は拒否される）")
    try:
        the_plan = load_plan(plan)
        estimate = estimate_stage(the_plan, stage)
        shard_pair = parse_shard(shard) if shard else None
    except PlanError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not live:
        typer.echo(json.dumps(estimate, ensure_ascii=False, indent=2))
        return
    try:
        check_gate(stage, approved)
    except PlanError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if mode == "record" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("--live (record) には ANTHROPIC_API_KEY が要る")
    if mode == "record" and estimate["budget_usd"] is None:
        raise typer.BadParameter("--live (record) には AGENTEVAL_BUDGET_USD（累積上限）が要る")
    if mode == "record" and not estimate["budget_fits_remaining_conservative"]:
        typer.echo(json.dumps(estimate, ensure_ascii=False, indent=2))
        raise typer.BadParameter(
            "累積上限が保守見積り（×1.5）の残りを賄えない。上限を上げるのはユーザーの承認後"
        )
    typer.echo(json.dumps({"estimate": estimate}, ensure_ascii=False, indent=2))
    summary = run_stage(the_plan, stage, mode=cast("Any", mode), shard=shard_pair)
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["stopped"] or summary["blocked_partial"] or summary["incomplete"] > 0:
        typer.echo(
            "[未完了] 予算停止・途中終了・未実行の run がある（上の summary を見る）", err=True
        )
        raise typer.Exit(code=1)


@coding_app.command("report")
def coding_report(
    stage: Annotated[str, typer.Option("--stage", help="pilot | opcheck | opcheck<N>")] = "pilot",
    only: Annotated[
        str, typer.Option("--only", help="spec: 特殊化検出の発火数・発火率だけを出す（操作確認）")
    ] = "",
    plan: Annotated[Path, typer.Option("--plan")] = CODING_PLAN_DEFAULT,
) -> None:
    """パイロット・操作確認の集計。`--stage opcheck<N> --only spec` は合格率・W 率を出さない（ADR-037）。"""
    from agenteval.reports.coding_corpus import (
        PlanError,
        is_opcheck_stage,
        load_plan,
        stage_report,
    )

    if stage != "pilot" and not is_opcheck_stage(stage):
        raise typer.BadParameter(
            "--stage は pilot | opcheck | opcheck<N> だけ（corpus の集計は実験スクリプトが行う）"
        )
    if only not in ("", "spec"):
        raise typer.BadParameter("--only は spec だけ")
    if is_opcheck_stage(stage) and only != "spec":
        raise typer.BadParameter(
            "opcheck では合格率・W 率を見ない（ADR-037 判断 2）。--only spec を付ける"
        )
    try:
        report = stage_report(load_plan(plan), stage, only_spec=only == "spec")
    except PlanError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("ptscorpus")
def pts_corpus(
    repeats: Annotated[int, typer.Option("--repeats")] = 5,
    workers: Annotated[int, typer.Option("--workers")] = 4,
    family: Annotated[str, typer.Option("--family", help="この族だけ作り直す")] = "",
) -> None:
    """合成変更（原典 3.8 コールドスタート）の PTS 専用コーパスを sim で作る。"""
    from agenteval.pts.synthetic import build_corpus

    summary = build_corpus(repeats=repeats, workers=workers, only_family=family or None)
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command()
def pages() -> None:
    """既存の結果 JSON から結果ページを日英とも描き直す（実験は再実行しない）。"""
    from agenteval.reports.pages import write
    from agenteval.reports.verify import load_registry, load_result

    registry = load_registry()
    written: list[str] = []
    skipped: list[str] = []
    untranslated: list[str] = []
    for entry in registry:
        result = load_result(entry["id"])
        if result is None:
            skipped.append(entry["id"])
            continue
        paths = write(entry, result)
        written.extend(p.name for p in paths)
        if len(paths) == 1:
            untranslated.append(entry["id"])
    typer.echo(
        json.dumps(
            {
                "written": len(written),
                "no_result_json": skipped,
                "missing_english": untranslated,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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
