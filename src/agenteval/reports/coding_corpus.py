"""コーディング環境（第 1 段）のコーパス計画・見積り・実行・集計。

`coding/corpus.yaml`（段・seed・版・タスク集合・反復）を読み、次を行う。
  - `plan_units`: (段, タスク, 版, 反復) の全組合せと run_id
  - `estimate_stage`: `--dry-run` の見積り（pricing.yaml の単価と run あたりの仮定。保守 ×1.5 も出す）
  - `run_stage`: live（`record` モード・塩付き）の実行。`auto` は使わない（コーディングのループが拒否する）
  - `require_stage_runs`: live の実験（EC-B2・R1・R2）が段の run だけを読むための入口。
    run が無ければ自分で作らず、案内付きで止まる。予算停止の run（.partial）があれば止まる
  - `stage_report`: W3 のパイロットと操作確認の集計（`coding report`）

office 側の `reports/corpus.py`（`./corpus.yaml`）とは別物である（ADR-039）。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NamedTuple

import numpy as np
import yaml

from agenteval.core.coding_registry import (
    coding_run_id,
    load_coding_tasks,
    load_coding_versions,
    load_task_sets,
    mode_dir_of,
)
from agenteval.core.coding_store import (
    load_coding_outcome,
    load_coding_runs,
    partial_trace_paths,
    trace_path,
)
from agenteval.core.coding_types import (
    ACC_FINISHED,
    CODING_DATA_ROOT,
    CORPUS_PLAN_PATH,
    REPO_FIXTURE_ROOT,
    CodingOutcome,
    CodingTask,
    CodingVersion,
    SnapshotManifest,
)
from agenteval.core.schema import Run
from agenteval.econ.outcome import SpecializationReport, classify, detect_specialization
from agenteval.llm.cost import BudgetExceeded, BudgetGuard, Pricing, ledger_total_usd, load_pricing
from agenteval.llm.models import model_id

ModeDir = Literal["live", "replay", "sim"]
UnitState = Literal["done", "partial", "missing"]
# 保守係数（TASK.md 6.2 節の「保守（×1.5）」）。corpus.yaml の estimate.conservative_factor が優先する
DEFAULT_CONSERVATIVE_FACTOR = 1.5
# コーパスのタスク数（EC-R1・EC-R2 の MDE の計算に使う。TASK.md・EC-R1.md の 24）
CORPUS_TASKS = 24


# live の段と、それを始めてよい承認済みのゲート（TASK.md 6.1 節: G1 = パイロット・操作確認、G2 = コーパス）
STAGE_GATES = {"pilot": "G1", "opcheck": "G1", "corpus": "G2"}


def required_gate(stage: str) -> str | None:
    """live の段なら、始めるのに要る承認済みのゲート（G1 | G2）。live でない段（unit・offline など）は None。"""
    return STAGE_GATES.get(stage)


def check_gate(stage: str, approved: str) -> None:
    """live の段を record・replay で始めるときのゲートの確認。対応しない承認では PlanError（ゲートの迂回を防ぐ）。"""
    need = required_gate(stage)
    if need is not None and approved != need:
        raise PlanError(
            f"段 {stage} は承認済みのゲート {need} が要る（--approved {need}）。受け取った値: {approved!r}"
            "（G1 = パイロット・操作確認、G2 = コーパス。承認は --dry-run の見積りを提示して得る）"
        )


class PlanError(ValueError):
    """コーパス計画の誤り（未知の段・集合・版など）。"""


class StageIncompleteError(RuntimeError):
    """段の run が足りない。実験は自分で run を作らず、案内付きで止まる。"""


class PartialRunsError(StageIncompleteError):
    """予算停止（.partial）などの途中終了の run がある。PENDING のまま止める（ADR-040）。"""


class RunUnit(NamedTuple):
    """コーパスの 1 run（段・タスク・版・反復）。"""

    stage: str
    task_id: str
    version_id: str
    repeat: int
    run_id: str


# ---------------------------------------------------------------------------
# 計画の読み込み
# ---------------------------------------------------------------------------


def load_plan(path: Path | None = None) -> dict[str, Any]:  # Any: YAML の木。下で検査する
    """`coding/corpus.yaml` を読む。トップレベルと stages が辞書でなければ PlanError。"""
    target = path or CORPUS_PLAN_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
        raise PlanError(f"{target}: stages を持つ辞書でなければならない")
    return {str(k): v for k, v in data.items()}


def stage_of(plan: Mapping[str, Any], stage: str) -> dict[str, Any]:  # Any: 段の YAML
    """段の定義。未知の段は PlanError。"""
    stages = plan["stages"]
    if stage not in stages:
        raise PlanError(f"未知の段: {stage}（{sorted(stages)}）")
    spec = stages[stage]
    if not isinstance(spec, dict) or "seed" not in spec or "mode" not in spec:
        raise PlanError(f"段 {stage} に seed と mode が要る")
    return dict(spec)


def stage_seed(plan: Mapping[str, Any], stage: str) -> int:
    return int(stage_of(plan, stage)["seed"])


def plan_units(
    plan: Mapping[str, Any],
    stage: str,
    *,
    task_sets: Mapping[str, Any] | None = None,
    versions: Collection[str] | None = None,
) -> list[RunUnit]:
    """段の run を計画の順（版 → タスク集合の順 → 反復）に展開する。

    `versions` を渡すとその版だけに絞る。sim の段（run の中身が計画に無い）は PlanError。
    """
    spec = stage_of(plan, stage)
    if spec["mode"] != "live":
        raise PlanError(
            f"段 {stage} は mode={spec['mode']}。run の中身は各実験スクリプトと W2 が決めるので、"
            "計画から展開できない"
        )
    sets = task_sets if task_sets is not None else load_task_sets()
    units: list[RunUnit] = []
    for entry in spec.get("runs", []):
        version_id = str(entry["version"])
        if versions is not None and version_id not in versions:
            continue
        set_name = str(entry["tasks"])
        tasks = sets.get(set_name)
        if not isinstance(tasks, list):
            raise PlanError(f"タスク集合 {set_name!r} が task_sets.yaml に無い（リストでない）")
        for task_id in tasks:
            for repeat in range(int(entry["repeats"])):
                units.append(
                    RunUnit(
                        stage,
                        str(task_id),
                        version_id,
                        repeat,
                        coding_run_id(str(task_id), version_id, stage, repeat),
                    )
                )
    return units


def unit_state(
    unit: RunUnit, *, data_root: Path = CODING_DATA_ROOT, mode_dir: ModeDir = "live"
) -> UnitState:
    """run の状態。完了（trace がある）、途中終了（.partial だけ）、無い。"""
    if trace_path(unit.run_id, mode_dir, data_root=data_root).exists():
        return "done"
    if partial_trace_paths(unit.run_id, mode_dir, data_root=data_root):
        return "partial"
    return "missing"


def unit_states(
    units: Sequence[RunUnit], *, data_root: Path = CODING_DATA_ROOT, mode_dir: ModeDir = "live"
) -> dict[UnitState, list[RunUnit]]:
    out: dict[UnitState, list[RunUnit]] = {"done": [], "partial": [], "missing": []}
    for unit in units:
        out[unit_state(unit, data_root=data_root, mode_dir=mode_dir)].append(unit)
    return out


# ---------------------------------------------------------------------------
# 実験が読む入口
# ---------------------------------------------------------------------------


def corpus_command(stage: str, *, approved: str) -> str:
    """run が無いときに案内するコマンド。"""
    return (
        f"uv run agenteval coding corpus --plan coding/corpus.yaml --stage {stage} --dry-run"
        f"  # 見積りを出して承認（{approved}）を得てから --live --approved {approved} --mode record"
    )


def require_stage_runs(
    stage: str,
    *,
    versions: Collection[str] | None = None,
    plan: Mapping[str, Any] | None = None,
    task_sets: Mapping[str, Any] | None = None,
    data_root: Path = CODING_DATA_ROOT,
    mode_dir: ModeDir = "live",
    approved: str = "G2",
) -> list[Run]:
    """段の live の run を読む。計画の run が 1 件でも欠けていれば止まる（自分では作らない）。

    - 予算停止（.partial）の run があれば `PartialRunsError`。実験は PENDING のまま（結果 JSON を書かない）
    - 欠けている run があれば `StageIncompleteError`（欠けた件数と案内付き）
    - 読むのは段の run だけ（パイロット・操作確認の run を混ぜない。ADR-033 判断 5、ADR-037 判断 5）
    """
    the_plan = plan if plan is not None else load_plan()
    units = plan_units(the_plan, stage, task_sets=task_sets, versions=versions)
    if not units:
        raise PlanError(f"段 {stage} に対象の run が無い（versions={sorted(versions or [])}）")
    states = unit_states(units, data_root=data_root, mode_dir=mode_dir)
    if states["partial"]:
        raise PartialRunsError(
            f"段 {stage} に予算停止などで途中終了した run が {len(states['partial'])} 件ある"
            f"（例: {states['partial'][0].run_id}）。実験は PENDING のまま止める（ADR-040）。"
            "やり直しには data/coding/snapshots/ の該当 run の削除が要るので、ユーザーの承認を得る"
        )
    if states["missing"]:
        per_version = Counter(u.version_id for u in states["missing"])
        raise StageIncompleteError(
            f"段 {stage} の run が {len(states['missing'])}/{len(units)} 件欠けている"
            f"（版別: {dict(per_version)}、例: {states['missing'][0].run_id}）。"
            f"実験は run を作らない。次のコマンドで案内に従う: {corpus_command(stage, approved=approved)}"
        )
    wanted = {u.run_id for u in units}
    runs = load_coding_runs(
        mode_dir,
        version_ids=set(versions) if versions is not None else None,
        stages={stage},
        data_root=data_root,
    )
    return [r for r in runs if r.run_id in wanted]


# ---------------------------------------------------------------------------
# 見積り（--dry-run）
# ---------------------------------------------------------------------------


def estimate_run_usd(
    version: CodingVersion,
    prices: Mapping[str, float],
    assumptions: Mapping[str, Any],  # Any: corpus.yaml の estimate（数値）
) -> float:
    """1 run の名目の費用（USD）。単価は pricing.yaml のモデル別の値（`prices`）。

    ステップ m のリクエストは、接頭辞（常に温かい）＋ 初回ユーザーメッセージ ＋ 先行ブロック k < m で、
    history_cache on の版はブロックを初回書き込み・以降読み出し、off の版は接頭辞より後ろを通常入力で数える。
    出力は毎ステップ output_tokens_per_step。SPEC.md 9.1 節の位置モデルと同じ形（`counterfactual_cost`）。
    """
    n = int(assumptions["steps_per_run"])
    prefix = float(assumptions["prefix_tokens"])
    user0 = float(assumptions["user0_tokens"])
    block = float(assumptions["new_tokens_per_step"])
    out = float(assumptions["output_tokens_per_step"])
    p_in, p_out = prices["input"], prices["output"]
    p_w, p_r = prices["cache_write_5m"], prices["cache_read"]
    total = 0.0
    for m in range(n):
        total += prefix * p_r + out * p_out
        if not version.harness.history_cache:
            total += (user0 + block * m) * p_in
            continue
        total += user0 * (p_w if m == 0 else p_r)
        for k in range(m):
            total += block * (p_w if k + 1 == m else p_r)
    return total / 1_000_000.0


def estimate_stage(
    plan: Mapping[str, Any],
    stage: str,
    *,
    data_root: Path = CODING_DATA_ROOT,
    mode_dir: ModeDir = "live",
    task_sets: Mapping[str, Any] | None = None,
    versions: Mapping[str, CodingVersion] | None = None,
    pricing: Pricing | None = None,
    budget_usd: float | None = None,
    ledger_usd: float | None = None,
) -> dict[str, Any]:  # Any: 表示用の JSON
    """段の `--dry-run` 見積り。API は呼ばない。

    名目と保守（×factor）、すでに完了している run を除いた残りの見積り、台帳の累積と上限に対する余裕を出す。
    仮定は `coding/corpus.yaml` の estimate。単価は pricing.yaml だけから読む。
    """
    units = plan_units(plan, stage, task_sets=task_sets)
    assumptions = plan.get("estimate") or {}
    factor = float(assumptions.get("conservative_factor", DEFAULT_CONSERVATIVE_FACTOR))
    all_versions = versions if versions is not None else load_coding_versions()
    the_pricing = pricing if pricing is not None else load_pricing()
    states = unit_states(units, data_root=data_root, mode_dir=mode_dir)
    pending_ids = {u.run_id for u in [*states["partial"], *states["missing"]]}
    by_version: dict[str, dict[str, Any]] = {}
    for unit in units:
        version = all_versions[unit.version_id]
        model = model_id(version.model)
        per_run = estimate_run_usd(version, the_pricing.models[model], assumptions)
        row = by_version.setdefault(
            unit.version_id,
            {
                "model": model,
                "history_cache": version.harness.history_cache,
                "runs": 0,
                "remaining_runs": 0,
                "usd_per_run": round(per_run, 4),
            },
        )
        row["runs"] += 1
        if unit.run_id in pending_ids:
            row["remaining_runs"] += 1
    steps = int(assumptions.get("steps_per_run", 0))
    nominal = sum(r["runs"] * r["usd_per_run"] for r in by_version.values())
    remaining = sum(r["remaining_runs"] * r["usd_per_run"] for r in by_version.values())
    spent = ledger_usd if ledger_usd is not None else ledger_total_usd()
    if budget_usd is None:
        env = os.environ.get("AGENTEVAL_BUDGET_USD")
        budget_usd = float(env) if env else None
    out: dict[str, Any] = {
        "stage": stage,
        "seed": stage_seed(plan, stage),
        "runs": len(units),
        "done": len(states["done"]),
        "partial": len(states["partial"]),
        "remaining_runs": len(states["partial"]) + len(states["missing"]),
        "calls_nominal": len(units) * steps,
        "usd_nominal": round(nominal, 2),
        "usd_conservative": round(nominal * factor, 2),
        "usd_remaining_nominal": round(remaining, 2),
        "usd_remaining_conservative": round(remaining * factor, 2),
        "conservative_factor": factor,
        "by_version": by_version,
        "ledger_usd": round(spent, 4),
        "budget_usd": budget_usd,
        "reference_usd_per_run": assumptions.get("reference_usd_per_run"),
        "note": "名目は corpus.yaml の run あたりの仮定と pricing.yaml の単価から計算した値。"
        "実測はパイロットで置き換える。API は呼んでいない",
    }
    if budget_usd is None:
        out["budget_fits_remaining_conservative"] = None
        out["budget_note"] = "AGENTEVAL_BUDGET_USD が未設定なので live は始まらない"
    else:
        out["budget_left_usd"] = round(budget_usd - spent, 4)
        out["budget_fits_remaining_conservative"] = bool(
            budget_usd - spent >= out["usd_remaining_conservative"]
        )
    return out


# ---------------------------------------------------------------------------
# 実行（live は record モード。auto は使わない）
# ---------------------------------------------------------------------------


def parse_shard(text: str) -> tuple[int, int]:
    """`i/n`（0 ≤ i < n）。"""
    try:
        left, right = text.split("/")
        index, count = int(left), int(right)
    except ValueError as exc:
        raise PlanError(f"--shard は i/n の形式: {text!r}") from exc
    if count < 1 or not 0 <= index < count:
        raise PlanError(f"--shard は 0 <= i < n: {text!r}")
    return index, count


def shard_units(units: Sequence[RunUnit], shard: tuple[int, int] | None) -> list[RunUnit]:
    """計画の順の位置 mod n で分ける（版・タスクが偏らない）。"""
    if shard is None:
        return list(units)
    index, count = shard
    return [u for pos, u in enumerate(units) if pos % count == index]


def run_stage(
    plan: Mapping[str, Any],
    stage: str,
    *,
    mode: Literal["record", "replay"] = "record",
    shard: tuple[int, int] | None = None,
    guard: BudgetGuard | None = None,
    data_root: Path = CODING_DATA_ROOT,
    task_sets: Mapping[str, Any] | None = None,
    stop_after: int | None = None,
) -> dict[str, Any]:  # Any: 表示用の JSON
    """段の未完了の run を順に実行して保存する。完了済みの run は飛ばす（再開できる）。

    予算停止（BudgetExceeded）が出たら波を止めて報告する（上限を上げるのはユーザーの承認後。TASK.md 6.3）。
    途中終了（.partial）の run は触らず、報告に載せる（やり直しは snapshots の削除の承認が要る）。
    `stop_after` は実行する run の数の上限（試験用）。
    """
    from agenteval.agent.coding_loop import (
        CodingRunOptions,
        RunDirectoryExistsError,
        run_coding_task,
    )

    spec = stage_of(plan, stage)
    seed = int(spec["seed"])
    tasks = load_coding_tasks()
    versions = load_coding_versions()
    units = shard_units(plan_units(plan, stage, task_sets=task_sets), shard)
    mode_dir = mode_dir_of("live" if mode == "record" else "replay")
    the_guard = guard if guard is not None else BudgetGuard()
    summary: dict[str, Any] = {
        "stage": stage,
        "mode": mode,
        "seed": seed,
        "shard": f"{shard[0]}/{shard[1]}" if shard else None,
        "planned": len(units),
        "skipped_done": 0,
        "blocked_partial": [],
        "incomplete": 0,
        "ran": 0,
        "passed_hidden": 0,
        "usd": 0.0,
        "stopped": None,
    }
    for unit in units:
        state = unit_state(unit, data_root=data_root, mode_dir=mode_dir)
        if state == "done":
            summary["skipped_done"] += 1
            continue
        if state == "partial":
            summary["blocked_partial"].append(unit.run_id)
            continue
        if stop_after is not None and summary["ran"] >= stop_after:
            summary["stopped"] = "stop_after"
            break
        try:
            run, _outcome = run_coding_task(
                tasks[unit.task_id],
                versions[unit.version_id],
                CodingRunOptions(
                    mode=mode,
                    stage=stage,
                    repeat=unit.repeat,
                    seed=seed,
                    budget=the_guard,
                    data_root=data_root,
                ),
            )
        except BudgetExceeded as exc:
            summary["stopped"] = f"BudgetExceeded: {exc}（run: {unit.run_id}）"
            break
        except RunDirectoryExistsError as exc:
            summary["blocked_partial"].append(f"{unit.run_id}: {exc}")
            continue
        summary["ran"] += 1
        summary["passed_hidden"] += int(run.passed())
        summary["usd"] = round(summary["usd"] + run.cost.usd, 4)
    # この shard の計画のうち、完了していない run の数（予算停止・途中終了・飛ばした分を含む）
    summary["incomplete"] = summary["planned"] - summary["skipped_done"] - summary["ran"]
    return summary


# ---------------------------------------------------------------------------
# 集計（coding report）
# ---------------------------------------------------------------------------


def _timed_out(run: Run) -> bool:
    """run_tests / run_shell の結果に timed_out: true があるか。"""
    for step in run.steps:
        for result in step.tool_results:
            try:
                payload = json.loads(result.content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("timed_out") is True:
                return True
    return False


def estimate_prefix_tokens(runs: Sequence[Run]) -> int | None:
    """system＋ツール定義のトークン数 P の推定。

    `history_cache: on` の run のステップ 0 は、接頭辞が温かければ cache_read が接頭辞の長さに等しい
    （user0 の区切りは書き込み）。段の run の正の cache_read の最頻値を P とする。
    正の値が 1 つも無ければ None（H3 の残差ではなく、履歴キャッシュの作動の確認だけに使う）。
    """
    values = [
        r.steps[0].usage.cache_read_input_tokens
        for r in runs
        if r.steps and r.steps[0].usage.cache_read_input_tokens > 0
    ]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def history_cache_inactive(run: Run, prefix_tokens: int) -> bool:
    """2 ステップ目以降の cache_read が接頭辞の長さ以下（履歴部分が 0）のステップがあるか。

    実装の確認の数え上げで、H3 の残差ではない（EC-B2.md: パイロットでは残差を計算しない）。
    """
    return any(s.usage.cache_read_input_tokens <= prefix_tokens for s in run.steps[1:])


def _spec_reports(
    runs: Sequence[Run],
    tasks: Mapping[str, CodingTask],
    *,
    data_root: Path,
    mode_dir: ModeDir,
    fixture_root: Path,
) -> tuple[dict[str, SpecializationReport], list[str]]:
    """bugfix の run の特殊化検出。目録・blob が読めない run は skipped に理由付きで載せる。"""
    from agenteval.env.repo import RepoEnv

    reports: dict[str, SpecializationReport] = {}
    skipped: list[str] = []
    cache: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    for run in runs:
        task = tasks.get(run.task_id)
        if task is None or task.kind != "bugfix":
            continue
        try:
            outcome: CodingOutcome = load_coding_outcome(run.run_id, mode_dir, data_root=data_root)
            if not outcome.final_snapshot:
                skipped.append(f"{run.run_id}: final_snapshot が無い")
                continue
            manifest = SnapshotManifest.model_validate_json(
                Path(outcome.final_snapshot).read_text(encoding="utf-8")
            )
            if task.id not in cache:
                cache[task.id] = (
                    RepoEnv.initial_files(task, fixture_root),
                    RepoEnv.visible_test_sources(task, fixture_root),
                )
            t0, visible = cache[task.id]
            repo_entries = manifest.areas.get("repo", {})
            paths = [p for p in outcome.touched_files if p in repo_entries]
            t1 = RepoEnv.manifest_texts(manifest, paths, data_root / "blobs")
            reports[run.run_id] = detect_specialization(run.run_id, task, t0, t1, visible)
        except (FileNotFoundError, ValueError, OSError) as exc:
            skipped.append(f"{run.run_id}: {type(exc).__name__}: {exc}")
    return reports, skipped


def _sigma_materials(
    runs: Sequence[Run], tasks: Mapping[str, CodingTask]
) -> dict[str, Any]:  # Any: 表示用の JSON
    """EC-R1.md「パイロットで計算する量」の材料（W 率の異質性 τ_W^2、σ_d と MDE の格子、費用の反復間 SD σ_c）。"""
    bugfix = [r for r in runs if tasks[r.task_id].kind == "bugfix"]
    by_task: dict[str, list[Run]] = {}
    for run in bugfix:
        by_task.setdefault(run.task_id, []).append(run)
    out: dict[str, Any] = {"n_tasks": len(by_task)}
    reps = [sorted(rs, key=lambda r: r.repeat) for rs in by_task.values() if len(rs) >= 2]
    if not reps:
        out["note"] = "反復が 2 未満のタスクだけで、τ_W^2 と σ_c は計算できない"
        return out
    m = min(len(rs) for rs in reps)
    w_hat = np.array([sum(classify(r) == "W" for r in rs[:m]) / m for rs in reps])
    inner = float(np.mean(w_hat * (1.0 - w_hat))) / (m - 1) if m > 1 else 0.0
    tau_sq = max(0.0, float(np.var(w_hat, ddof=1)) - inner) if len(w_hat) > 1 else 0.0
    w01 = float(np.mean(w_hat))
    grid: list[dict[str, float]] = []
    for delta in (0.10, 0.15, 0.20):
        w13 = min(1.0, w01 + delta)
        sigma_d = math.sqrt(tau_sq + (w01 * (1 - w01) + w13 * (1 - w13)) / 6.0)
        grid.append(
            {
                "w13_minus_w01": delta,
                "sigma_d": round(sigma_d, 4),
                "mde": round(2.8 * sigma_d / math.sqrt(CORPUS_TASKS), 4),
            }
        )
    costs = np.array([[rs[0].cost.usd, rs[1].cost.usd] for rs in reps])
    sigma_c = math.sqrt(float(np.sum((costs[:, 0] - costs[:, 1]) ** 2)) / (2.0 * len(costs)))
    out.update(
        {
            "repeats_used": m,
            "w_rate_mean": round(w01, 4),
            "tau_w_sq": round(tau_sq, 5),
            "sigma_d_grid": grid,
            "sigma_c_usd": round(sigma_c, 5),
            "mde_basis": "MDE = 2.8 sigma_d / sqrt(24)（両側 5%、検出力 80%。c13 の W 率は見ず格子で置く）",
        }
    )
    return out


def stage_report(
    plan: Mapping[str, Any],
    stage: str,
    *,
    only_spec: bool = False,
    data_root: Path = CODING_DATA_ROOT,
    mode_dir: ModeDir = "live",
    fixture_root: Path = REPO_FIXTURE_ROOT,
    task_sets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:  # Any: 表示用の JSON
    """段の run の集計（W3-P5・W3-A2 用）。

    `only_spec=True`（操作確認 `--stage opcheck --only spec`）は、**特殊化検出の発火数・発火率だけ**を出す。
    合格率・S/W/F・W 率・費用は出さない（ADR-037 判断 2: 操作確認で c13 の W 率と合格率を見ない）。
    通常（`--stage pilot`）は合格率・σ_d の材料・1 run の実費・finish 未呼び出し率・タイムアウト率・
    spec_literal 発火率・履歴キャッシュが作動していない run の数を出す。EC-B2 H3 の残差は出さない。
    """
    tasks = load_coding_tasks()
    units = plan_units(plan, stage, task_sets=task_sets)
    states = unit_states(units, data_root=data_root, mode_dir=mode_dir)
    runs = load_coding_runs(mode_dir, stages={stage}, data_root=data_root)
    planned_ids = {u.run_id for u in units}
    runs = [r for r in runs if r.run_id in planned_ids]
    versions = sorted({r.version_id for r in runs})
    out: dict[str, Any] = {
        "stage": stage,
        "seed": stage_seed(plan, stage),
        "planned": len(units),
        "done": len(states["done"]),
        "partial": [u.run_id for u in states["partial"]],
        "missing": len(states["missing"]),
        "versions": {},
    }
    reports, skipped = _spec_reports(
        runs, tasks, data_root=data_root, mode_dir=mode_dir, fixture_root=fixture_root
    )
    for version_id in versions:
        vruns = [r for r in runs if r.version_id == version_id]
        bugfix = [r for r in vruns if tasks[r.task_id].kind == "bugfix"]
        fired = sorted(
            r.run_id for r in bugfix if r.run_id in reports and reports[r.run_id].spec_literal
        )
        spec_block = {
            "n_bugfix_runs": len(bugfix),
            "n_detected": sum(r.run_id in reports for r in bugfix),
            "spec_literal_fired": len(fired),
            "spec_literal_rate": round(len(fired) / len(bugfix), 4) if bugfix else None,
            "fired_runs": fired,
        }
        if only_spec:
            out["versions"][version_id] = {"n_runs": len(vruns), "spec": spec_block}
            continue
        counts = Counter(classify(r) for r in bugfix)
        n_b = len(bugfix)
        prefix = estimate_prefix_tokens(vruns)
        hc_inactive = None
        if prefix is not None:
            hc_inactive = sum(history_cache_inactive(r, prefix) for r in vruns if len(r.steps) > 1)
        by_kind: dict[str, dict[str, float | int]] = {}
        for kind in sorted({tasks[r.task_id].kind for r in vruns}):
            kr = [r for r in vruns if tasks[r.task_id].kind == kind]
            by_kind[kind] = {
                "n": len(kr),
                "hidden_pass_rate": round(sum(r.passed() for r in kr) / len(kr), 4),
            }
        out["versions"][version_id] = {
            "n_runs": len(vruns),
            "bugfix": {
                "n": n_b,
                "S": counts["S"],
                "W": counts["W"],
                "F": counts["F"],
                "pi_S": round(counts["S"] / n_b, 4) if n_b else None,
                "pi_W": round(counts["W"] / n_b, 4) if n_b else None,
                "pi_F": round(counts["F"] / n_b, 4) if n_b else None,
                "baseline_target": "pi_S が 0.5〜0.8 に入るか（入らなければタスクの難度を調整する。モデルは変えない）",
            },
            "by_kind": by_kind,
            "sigma_materials": _sigma_materials(vruns, tasks),
            "usd_per_run_mean": round(sum(r.cost.usd for r in vruns) / len(vruns), 5),
            "finish_not_called_rate": round(
                sum(
                    not (r.outcome and r.outcome.acceptance_results.get(ACC_FINISHED))
                    for r in vruns
                )
                / len(vruns),
                4,
            ),
            "timeout_run_rate": round(sum(_timed_out(r) for r in vruns) / len(vruns), 4),
            "spec": spec_block,
            "prefix_tokens_estimate": prefix,
            "history_cache_inactive_runs": hc_inactive,
            "history_cache_note": (
                "2 ステップ目以降で cache_read が接頭辞の長さ以下のステップがある run の数（実装の確認。"
                "H3 の残差ではない）。接頭辞は段の正の step-0 cache_read の最頻値"
            ),
        }
    if skipped:
        out["spec_skipped"] = skipped
    if only_spec:
        out["note"] = (
            "操作確認: 特殊化検出の発火数と発火率だけを出す（合格率・W 率は出さない。ADR-037 判断 2）"
        )
    return out


# ---------------------------------------------------------------------------
# exp --dry-run
# ---------------------------------------------------------------------------


def experiment_dry_run(
    experiment_id: str, *, seed: int = 20260913
) -> dict[str, Any]:  # Any: 表示用
    """`agenteval exp <id> --dry-run`: 実験を実行せずに、依存する入力と費用の見込みを返す。

    実験スクリプトが `dry_run(seed)` を持てばそれを呼ぶ（章 C の live 実験は、段の run の有無・案内・
    実験自身が作る run 数（0）を返す）。持たなければ registry の情報（来歴・予算・基準）だけを返す。
    API は呼ばない。結果 JSON も結果ページも書かない。
    """
    import importlib.util
    import sys

    from agenteval.reports.runner import find_script, normalize_id
    from agenteval.reports.verify import load_registry

    stem = normalize_id(experiment_id)
    entry = next((e for e in load_registry() if normalize_id(str(e["id"])) == stem), None)
    if entry is None:
        raise KeyError(f"registry.yaml に無い実験: {experiment_id}")
    path = find_script(experiment_id)
    out: dict[str, Any] = {
        "id": entry["id"],
        "script": str(path.relative_to(path.parents[1])),
        "provenance": entry.get("provenance"),
        "budget_usd": entry.get("budget_usd", 0),
        "criteria": entry.get("criteria", []),
    }
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"読み込めない実験スクリプト: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    dry = getattr(module, "dry_run", None)
    if callable(dry):
        out["inputs"] = dry(seed=seed)
    else:
        out["inputs"] = None
        out["note"] = "このスクリプトに dry_run が無い。registry の予算（budget_usd）だけを示す"
    return out
