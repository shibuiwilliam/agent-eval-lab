"""run_tests の詳細出力の大きさをオフラインで測る（EC-B2 H2 の検出力を G2 で判断するための実測）。

c01_baseline（簡潔・末尾 80 行）と c04_verbose_ctx（詳細・切り詰めなし）が実際に `run_tests` から受け取る出力の
文字数を、sub10 の各タスクについて、変異コード（課題の初期状態）と正解パッチ適用後の作業ツリーで測る。
各側で `run_tests` を (i) 引数なし（可視テスト全体）と (ii) 失敗テストだけ（`failing_visible_tests`）で走らせる。

**ツールの本物をそのまま呼ぶ**: pytest の引数（-q --tb=short と -vv --tb=long -rA）と出力の切り詰めは
`repo_tools` の run_tests ツール（`build_toolset("c1")["run_tests"].run`）が版の `harness` から決める。
このスクリプトは引数を書き写さない。`CodingVersion` を `coding/versions/*.yaml` から読み、`ToolContext` に渡すだけ。
実行は Docker のサンドボックス（`--network none`）の中だけ。LLM は呼ばない（費用 0、来歴ラベルは simulated ではなく
offline の実測）。

実行: `uv run python scripts/measure_verbose_output.py [--out PATH] [--task-set sub10]`
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from agenteval.core.coding_registry import (
    load_coding_tasks,
    load_coding_versions,
    load_task_sets,
)
from agenteval.core.coding_types import (
    CODING_DATA_ROOT,
    REPO_FIXTURE_ROOT,
    CodingTask,
    CodingVersion,
    ExecutorFactory,
    ModeDir,
)
from agenteval.env.bugsynth import apply_unified
from agenteval.env.repo import RepoEnv
from agenteval.env.repo_tools import RunTestsArgs, ToolContext, ToolOutput, build_toolset
from agenteval.env.sandbox import cleanup_containers, docker_available, docker_executor_factory

# EC-B2.md の実測（run_tests 出力の文字/トークン比。来歴 live）
CHARS_PER_TOKEN = 2.86
BASELINE_VERSION = "c01_baseline"
VERBOSE_VERSION = "c04_verbose_ctx"
DEFAULT_TASK_SET = "sub10"
DEFAULT_OUT = CODING_DATA_ROOT / "measure" / "verbose_output.json"
# 使い捨ての run_id と作業領域の mode（live / replay のデータと混ざらない）
MODE_DIR: ModeDir = "sim"

Side = Literal["mutant", "gold"]
Subset = Literal["all", "failing"]
SIDES: tuple[Side, ...] = ("mutant", "gold")
SUBSETS: tuple[Subset, ...] = ("all", "failing")


@dataclass(frozen=True)
class Row:
    """run_tests 1 回の測定。chars は文字数（バイトではない）。"""

    task_id: str
    side: Side
    subset: Subset
    version_id: str
    selector: str | None
    exit_code: int
    n_passed: int
    n_failed: int
    n_error: int
    raw_chars: int  # 切り詰め前の出力
    shown_chars: int  # 切り詰め後の出力（モデルに見せる output フィールド）
    payload_chars: int  # tool_result 本文（JSON 全体。ループが履歴に入れる長さ）
    truncated: bool
    timed_out: bool


# --- 版・引数 ----------------------------------------------------------------------
def selector_for(task: CodingTask, subset: Subset) -> str | None:
    """subset に対応する selector（全体は None。失敗テストだけは nodeid の空白区切り）。"""
    if subset == "all":
        return None
    return " ".join(task.failing_visible_tests)


def tool_context(
    env: RepoEnv, task: CodingTask, version: CodingVersion, *, call_id: str
) -> ToolContext:
    """本番のループと同じ ToolContext を作る（版の harness がそのまま効く）。"""
    return ToolContext(
        env=env,
        task=task,
        version=version,
        run_id=env.run_id,
        step=1,
        call_id=call_id,
        harness_env={},
    )


def measure_once(
    env: RepoEnv,
    task: CodingTask,
    version: CodingVersion,
    side: Side,
    subset: Subset,
) -> Row:
    """既存の run_tests ツールをそのまま呼び、出力の大きさを記録する。"""
    selector = selector_for(task, subset)
    spec = build_toolset(version.toolset)["run_tests"]
    ctx = tool_context(env, task, version, call_id=f"measure-{side}-{subset}-{version.id}")
    out: ToolOutput = spec.run(RunTestsArgs(selector=selector), ctx)
    payload = out.payload
    summary = payload["summary"]
    if out.raw_chars is None or out.shown_chars is None:
        raise RuntimeError("run_tests が raw_chars / shown_chars を返さなかった")
    return Row(
        task_id=task.id,
        side=side,
        subset=subset,
        version_id=version.id,
        selector=selector,
        exit_code=int(payload["exit_code"]),
        n_passed=int(summary["passed"]),
        n_failed=int(summary["failed"]),
        n_error=int(summary["error"]),
        raw_chars=out.raw_chars,
        shown_chars=out.shown_chars,
        payload_chars=len(json.dumps(payload, ensure_ascii=False)),
        truncated=bool(payload["truncated"]),
        timed_out=bool(payload["timed_out"]),
    )


# --- 正解側の作業ツリー ------------------------------------------------------------
_PATCH_FILE_RE = re.compile(r"^--- a/(\S+)", re.MULTILINE)


def split_patch_by_file(patch: str) -> dict[str, str]:
    """複数ファイルの unified diff をファイルごとに分ける（`apply_unified` は単一ファイル用）。"""
    marks = list(_PATCH_FILE_RE.finditer(patch))
    parts: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(patch)
        parts[m.group(1)] = patch[m.start() : end]
    return parts


def apply_gold_patch(env: RepoEnv, task: CodingTask) -> list[str]:
    """変異コードの作業ツリーに正解パッチを当てる。当てたファイルを返す。"""
    parts = split_patch_by_file(task.gold_patch)
    if not parts or sorted(parts) != sorted(task.gold_files):
        raise ValueError(f"{task.id}: gold_patch の対象が gold_files と合わない: {sorted(parts)}")
    for rel, text in parts.items():
        before = env.read_area_file(f"repo:{rel}")
        if before is None:
            raise FileNotFoundError(f"{task.id}: 正解パッチの対象が作業ツリーに無い: {rel}")
        env.write_area_file(f"repo:{rel}", apply_unified(before, text))
    return sorted(parts)


def new_launch_id() -> str:
    """起動ごとに一意な 8 桁の 16 進数。"""
    return uuid.uuid4().hex[:8]


# --- 実行 --------------------------------------------------------------------------
def measure_task(
    task: CodingTask,
    versions: Sequence[CodingVersion],
    *,
    executor_factory_of: Callable[[str], ExecutorFactory],
    data_root: Path,
    fixture_root: Path = REPO_FIXTURE_ROOT,
    require_isolated: bool = True,
    launch_id: str | None = None,
) -> list[Row]:
    """1 タスクを、変異側・正解側の順に測る。作業領域は側ごとに作って片付ける。

    launch_id: 起動ごとに一意な値。run_id（= コンテナ名とラベル）に入れ、同時起動や再実行の重なりで
    コンテナ名が衝突したり、片方の cleanup がもう片方を消したりしないようにする。None なら作る。
    """
    launch = launch_id or new_launch_id()
    rows: list[Row] = []
    for side in SIDES:
        run_id = f"cd__measure__{launch}__{task.id}__{side}__r0"
        env = RepoEnv.create(
            task,
            run_id,
            executor_factory=executor_factory_of(run_id),
            mode_dir=MODE_DIR,
            data_root=data_root,
            fixture_root=fixture_root,
        )
        try:
            if require_isolated and not env.executor.network_isolated:
                raise RuntimeError("executor が --network none ではない（測定を中止する）")
            if side == "gold":
                apply_gold_patch(env, task)
            for subset in SUBSETS:
                for version in versions:
                    rows.append(measure_once(env, task, version, side, subset))
        finally:
            env.close()
    return rows


def _sum(rows: Sequence[Row], key: Literal["raw_chars", "shown_chars", "payload_chars"]) -> int:
    return sum(getattr(r, key) for r in rows)


def summarize(
    rows: Sequence[Row],
    *,
    baseline: str = BASELINE_VERSION,
    verbose: str = VERBOSE_VERSION,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> dict[str, Any]:  # Any: JSON にする入れ子の辞書
    """タスクごとの c04 − c01 と、側・subset ごとの合計・平均（文字とトークン換算）を作る。"""
    per_task: list[dict[str, Any]] = []
    totals: dict[str, Any] = {}
    task_ids = sorted({r.task_id for r in rows})
    for side in SIDES:
        for subset in SUBSETS:
            diffs: list[dict[str, int]] = []
            for tid in task_ids:
                pick = [r for r in rows if (r.task_id, r.side, r.subset) == (tid, side, subset)]
                base = next((r for r in pick if r.version_id == baseline), None)
                verb = next((r for r in pick if r.version_id == verbose), None)
                if base is None or verb is None:
                    continue
                diff = {
                    "shown_chars": verb.shown_chars - base.shown_chars,
                    "payload_chars": verb.payload_chars - base.payload_chars,
                    "raw_chars": verb.raw_chars - base.raw_chars,
                }
                diffs.append(diff)
                per_task.append(
                    {
                        "task_id": tid,
                        "side": side,
                        "subset": subset,
                        baseline: _row_summary(base),
                        verbose: _row_summary(verb),
                        "diff": diff,
                    }
                )
            n = len(diffs)
            entry: dict[str, Any] = {"n_tasks": n}
            for key in ("shown_chars", "payload_chars", "raw_chars"):
                total = sum(d[key] for d in diffs)
                mean = total / n if n else 0.0
                entry[key] = {
                    "diff_total": total,
                    "diff_mean": mean,
                    "diff_total_tokens": total / chars_per_token,
                    "diff_mean_tokens": mean / chars_per_token,
                }
            totals[f"{side}/{subset}"] = entry
    n_timed_out = sum(1 for r in rows if r.timed_out)
    warnings: list[str] = []
    if n_timed_out:
        warnings.append(
            f"timed_out の行が {n_timed_out} 件ある。出力が途中で切れており、文字数は過小評価になる"
        )
    return {
        "chars_per_token": chars_per_token,
        "n_rows": len(rows),
        "n_timed_out": n_timed_out,
        "warnings": warnings,
        "baseline": baseline,
        "verbose": verbose,
        "per_task": per_task,
        "totals": totals,
        "sums_by_version": {
            f"{side}/{subset}/{vid}": {
                "shown_chars": _sum(sel, "shown_chars"),
                "payload_chars": _sum(sel, "payload_chars"),
                "raw_chars": _sum(sel, "raw_chars"),
            }
            for side in SIDES
            for subset in SUBSETS
            for vid in (baseline, verbose)
            if (sel := [r for r in rows if (r.side, r.subset, r.version_id) == (side, subset, vid)])
        },
    }


def _row_summary(r: Row) -> dict[str, Any]:  # Any: JSON にする辞書
    return {
        "raw_chars": r.raw_chars,
        "shown_chars": r.shown_chars,
        "payload_chars": r.payload_chars,
        "truncated": r.truncated,
        "passed": r.n_passed,
        "failed": r.n_failed,
        "error": r.n_error,
        "exit_code": r.exit_code,
    }


def run_measurement(
    task_ids: Sequence[str],
    *,
    executor_factory_of: Callable[[str], ExecutorFactory],
    data_root: Path,
    fixture_root: Path = REPO_FIXTURE_ROOT,
    require_isolated: bool = True,
    on_task: Callable[[str], None] | None = None,
    launch_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON にする入れ子の辞書
    """指定タスクを全部測り、行と集計を持つ辞書を返す。launch_id は全タスクで共通（None なら作る）。"""
    launch = launch_id or new_launch_id()
    tasks = load_coding_tasks()
    versions_all = load_coding_versions()
    versions = [versions_all[BASELINE_VERSION], versions_all[VERBOSE_VERSION]]
    rows: list[Row] = []
    for tid in task_ids:
        if on_task is not None:
            on_task(tid)
        rows.extend(
            measure_task(
                tasks[tid],
                versions,
                executor_factory_of=executor_factory_of,
                data_root=data_root,
                fixture_root=fixture_root,
                require_isolated=require_isolated,
                launch_id=launch,
            )
        )
    summary = summarize(rows)
    return {
        "provenance": "offline-measured (Docker, --network none, no LLM, cost 0 USD)",
        "notes": {
            "provenance": (
                "LLM を使わないオフラインの実測。live / replay / simulated のいずれでもない"
            ),
            "chars_per_token": (
                f"換算比 {CHARS_PER_TOKEN} は EC-B2.md の run_tests 出力の実測値（c01 の簡潔な出力で"
                "測った比。来歴 live）。詳細出力では比が違いうる"
            ),
            "diff_total": (
                "diff_total は 1 回の呼び出しあたりの出力の大きさ（c04 − c01）の、タスクにわたる和。"
                "EC-B2 の ΣD_t（呼び出し回数と再送回数 n_i が要る）の直接の見積りではない"
            ),
        },
        "launch_id": launch,
        "warnings": list(summary["warnings"]),
        "task_ids": list(task_ids),
        "harness": {
            v.id: {
                "test_output_limit": v.harness.test_output_limit,
                "test_verbosity": v.harness.test_verbosity,
            }
            for v in versions
        },
        "rows": [asdict(r) for r in rows],
        "summary": summary,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="JSON の出力先")
    parser.add_argument("--task-set", default=DEFAULT_TASK_SET, help="coding/task_sets.yaml のキー")
    args = parser.parse_args(argv)
    if not docker_available():
        print("Docker かサンドボックスのイメージが無い。測定を中止する", file=sys.stderr)
        return 2
    task_ids = [str(t) for t in load_task_sets()[args.task_set]]
    # 作業領域は out の隣の一時ディレクトリ（リポジトリの中。Docker の共有パスに入る）
    args.out.parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="work-", dir=args.out.parent))
    started: list[str] = []
    launch = new_launch_id()  # 起動ごとに一意（同時起動のコンテナ名の衝突を避ける）

    def factory_of(run_id: str) -> ExecutorFactory:
        started.append(run_id)
        return docker_executor_factory(run_id, MODE_DIR)

    try:
        result = run_measurement(
            task_ids,
            executor_factory_of=factory_of,
            data_root=work_root,
            on_task=lambda tid: print(f"measuring {tid}", file=sys.stderr),
            launch_id=launch,
        )
    finally:
        for run_id in started:  # 自分の run のコンテナだけ消す（他の runner のものは触らない）
            cleanup_containers(run_id)
        shutil.rmtree(work_root, ignore_errors=True)
    for warning in result["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
