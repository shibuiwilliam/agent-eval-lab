"""EC-B2 コンテキスト・コスト帰属 — ツール出力の生涯コストと履歴キャッシュの事前予測。

仮説・植込み・対照・基準は `docs/results/EC-B2.md`（正本）と registry.yaml の EC-B2（基準は W3-A2 で確定。ADR-033）。

入力:
- 段 `corpus` の live の run だけ（c01_baseline・c04_verbose_ctx・c01o_no_history_cache・c12_null。パイロットの run は
  混ぜない）。**live の run は作らない**。欠けていれば案内付きで止まり、予算停止（.partial）の run があれば止まる
  （PENDING のまま）
- 対照 (a) のスクリプト化 run 2 本（段 `offline`、simulated、API 費用 0）だけはこの実験が作る。Docker が要る。既にあれば再利用する

来歴: 割り振り・台帳との照合・H2・H3 は live（既存の live run の再計算）。対照 (a) は simulated（合成 usage。
live の数値と混ぜない。絶対額を live と並べない）。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _coding_common import failure_caution, finalize_coding, json_safe, labeled

from agenteval.agent.coding_loop import CodingRunOptions, run_coding_task
from agenteval.agent.scripted import Script, ScriptedToolUse, ScriptStep
from agenteval.core.coding_registry import (
    coding_run_id,
    get_coding_version,
    load_coding_tasks,
    load_coding_versions,
    load_task_sets,
    mode_dir_of,
    prompt_text,
)
from agenteval.core.coding_store import load_coding_runs
from agenteval.core.coding_types import CODING_DATA_ROOT, CodingVersion, ExecutorFactory
from agenteval.core.schema import Run
from agenteval.econ.context_cost import (
    LEDGER_IDENTITY_TOLERANCE,
    RunAllocation,
    UnitPrices,
    allocate,
    cca_of_tool,
    cca_ratio,
    counterfactual_cost,
    history_cache_prediction_error,
    match_ledger,
    predicted_resend_ratio,
    verbose_attribution,
)
from agenteval.econ.outcome import cluster_bootstrap
from agenteval.env.repo_tools import toolset_hash_material
from agenteval.llm.cost import LedgerEntry, Pricing, load_pricing, read_ledger
from agenteval.llm.models import model_id
from agenteval.reports.coding_corpus import (
    history_cache_inactive,
    load_plan,
    plan_units,
    require_stage_runs,
    stage_seed,
    unit_states,
)

EXPERIMENT_ID = "EC-B2"
STAGE = "corpus"
OFFLINE_STAGE = "offline"
BASE, VERBOSE, OFF, NULL = (
    "c01_baseline",
    "c04_verbose_ctx",
    "c01o_no_history_cache",
    "c12_null",
)
VERSIONS = (BASE, VERBOSE, OFF, NULL)
TOOL = "run_tests"
# 対照 (a): 12 ツールステップ（0〜11）＋ finish。詳細出力は E 版がステップ 1、L 版が最後のツールステップ（11）
CONTROL_TOOL_STEPS = 12
EARLY_STEP, LATE_STEP = 1, 11
VERBOSE_CMD = "seq 1 2000"  # 約 8.9k 文字の出力（ツール出力の上限 20000 文字の内側）
CHEAP_CMD = "echo ok"
CONTROL_SCRIPT_IDS = {"early": "sc-b2-early", "late": "sc-b2-late"}
N_BOOT = 2000
MIN_LEDGER_MATCH_RATE = 0.95  # EC-B2P の ledger_match_rate の基準。これ未満なら恒等式の値を出さない
METHOD_FALLBACK = (
    "段 corpus の live の run の usage から位置モデルで課金を割り振り、台帳と照合する（H1）。c04 の詳細出力の"
    "コスト増の run_tests への帰属（H2）と、履歴キャッシュを外したコスト増の事前予測（H3、run 内の反実仮想）を測る。"
    "対照 (a) は同じ詳細出力を早く出す／遅く出すスクリプト化 run の対。詳細は docs/results/EC-B2.md。"
)


# ---------------------------------------------------------------------------
# 対照 (a) の台本と run
# ---------------------------------------------------------------------------


def control_script(position: Literal["early", "late"]) -> Script:
    """対照 (a) の台本。同じ 12 ツールステップ＋ finish で、詳細出力の位置だけが違う（SPEC.md 11.5 節）。

    ツール呼び出しはすべて `run_shell`（引数の違いは詳細出力の 1 回だけ）。他のステップは `echo ok`。
    台本のステップ数・テキストは両方で同一にして、帰属の比以外の差を作らない。
    """
    verbose_at = EARLY_STEP if position == "early" else LATE_STEP
    steps = [
        ScriptStep(
            text=f"手順 {i} を実行します。",
            tool_uses=[
                ScriptedToolUse(
                    name="run_shell", input={"cmd": VERBOSE_CMD if i == verbose_at else CHEAP_CMD}
                )
            ],
        )
        for i in range(CONTROL_TOOL_STEPS)
    ]
    steps.append(
        ScriptStep(
            text="完了を報告します。",
            tool_uses=[
                ScriptedToolUse(name="finish", input={"summary": "手順を完了した。", "claims": []})
            ],
        )
    )
    return Script(
        script_id=CONTROL_SCRIPT_IDS[position],
        description=f"EC-B2 対照 (a): 詳細出力を{'早く（ステップ 1）' if position == 'early' else '遅く（最後のツールステップ）'}出す",
        steps=steps,
    )


def run_control_pair(
    *,
    data_root: Path = CODING_DATA_ROOT,
    executor_factory: ExecutorFactory | None = None,
    seed: int | None = None,
) -> tuple[Run, Run]:
    """対照 (a) の 2 run を作る（既にあれば読む）。ハーネスは `c01o_no_history_cache`（off）を借りる。

    タスクは `sub10` の先頭（両 run で同じ）。段は `offline`。API を呼ばない（ScriptedAgent）。
    """
    version = get_coding_version(OFF)
    task = load_coding_tasks()[load_task_sets()["sub10"][0]]
    plan_seed = seed if seed is not None else stage_seed(load_plan(), OFFLINE_STAGE)
    mode_dir = mode_dir_of("sim")
    existing = {
        r.run_id: r for r in load_coding_runs(mode_dir, stages={OFFLINE_STAGE}, data_root=data_root)
    }
    out: list[Run] = []
    for position in ("early", "late"):
        script = control_script(position)  # type: ignore[arg-type]
        run_id = coding_run_id(task.id, version.id, OFFLINE_STAGE, 0, script.script_id)
        if run_id in existing:
            out.append(existing[run_id])
            continue
        run, _outcome = run_coding_task(
            task,
            version,
            CodingRunOptions(
                mode="sim",
                stage=OFFLINE_STAGE,
                repeat=0,
                seed=plan_seed,
                script=script,
                executor_factory=executor_factory,
                data_root=data_root,
            ),
        )
        out.append(run)
    return out[0], out[1]


def dry_run(seed: int = 20260913) -> dict[str, Any]:  # Any: 表示用
    """`exp --dry-run`: 依存する run の有無、この実験自身が作る run（対照 (a) の 2 本。simulated）。"""
    del seed
    plan = load_plan()
    units = plan_units(plan, STAGE, versions=VERSIONS)
    states = unit_states(units)
    return {
        "creates_live_runs": 0,
        "creates_simulated_runs": {
            "count": 2,
            "stage": OFFLINE_STAGE,
            "versions": OFF,
            "script_ids": list(CONTROL_SCRIPT_IDS.values()),
            "needs_docker": True,
            "usd": 0.0,
        },
        "reads": f"段 {STAGE} の live の run（版 {list(VERSIONS)}）だけ",
        "planned": len(units),
        "done": len(states["done"]),
        "partial": len(states["partial"]),
        "missing": len(states["missing"]),
        "ready": not states["partial"] and not states["missing"],
        "how_to_make_runs": "uv run agenteval coding corpus --stage corpus --dry-run（G2 の承認後に --live）",
    }


# ---------------------------------------------------------------------------
# 前提の検査
# ---------------------------------------------------------------------------


def prefix_tokens_of(off_runs: Sequence[Run]) -> int:
    """system＋ツール定義のトークン数 P = c01o の run のステップ 0 の T0 − in0（off では in0 が user0 に等しい）。

    c01o の run の間で一致しなければ ValueError。on の版のステップ 0 から求めてはいけない
    （区切りが user0 の末尾にも付き、in0 がほぼ 0 になる）。
    """
    values: set[int] = set()
    for run in off_runs:
        u = run.steps[0].usage
        values.add(
            u.input_tokens
            + u.cache_creation_input_tokens
            + u.cache_read_input_tokens
            - u.input_tokens
        )
    if len(values) != 1:
        raise ValueError(f"c01o の run の接頭辞のトークン数が一致しない: {sorted(values)}")
    return values.pop()


def check_same_tool_definitions(
    versions: Mapping[str, CodingVersion],
    runs: Mapping[str, Sequence[Run]],
) -> dict[str, Any]:  # Any: 検査の記録
    """c04 のツール定義（とプロンプト）が c01 と同一であることを確かめる（EC-B2.md: P を共通にする前提）。

    版の定義（ツール集合の schema の正規 JSON とプロンプト本文）が同一であること、および同じタスクの 1 ステップ目の
    総入力 T0 が c01 と c04 で一致すること（実測での確認）を調べる。一致しなければ ValueError。
    """
    base, verbose = versions[BASE], versions[VERBOSE]
    tools_same = toolset_hash_material(base.toolset) == toolset_hash_material(verbose.toolset)
    prompt_same = prompt_text(base) == prompt_text(verbose)

    def t0(run: Run) -> int:
        u = run.steps[0].usage
        return u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens

    base_t0 = {
        r.task_id: t0(r) for r in runs[BASE]
    }  # 同じタスクの T0 は反復で同じ（初回メッセージが同一）
    pairs = [(base_t0[r.task_id], t0(r)) for r in runs[VERBOSE] if r.task_id in base_t0]
    equal = sum(a == b for a, b in pairs)
    if not (tools_same and prompt_same) or equal != len(pairs):
        raise ValueError(
            "c04 のツール定義・プロンプトが c01 と同一でない"
            f"（ツール集合 {tools_same}、プロンプト {prompt_same}、T0 の一致 {equal}/{len(pairs)}）。"
            "接頭辞 P を共通にできない"
        )
    return {
        "toolset_identical": tools_same,
        "prompt_identical": prompt_same,
        "t0_equal": equal,
        "t0_pairs": len(pairs),
    }


# ---------------------------------------------------------------------------
# 計算（run・ファイルを読まない。単体テストできる）
# ---------------------------------------------------------------------------


def attribution_components(
    base_by_task: Mapping[str, Sequence[RunAllocation]],
    treated_by_task: Mapping[str, Sequence[RunAllocation]],
    *,
    tool: str = TOOL,
    n_boot: int,
    seed: int,
) -> dict[str, dict[str, float | int]]:
    """H2 の比 Σ N_t / Σ D_t の分子・分母と、ステップ数の差を、比と同じブートストラップで区間付きにする（記録のみ）。

    N_t・D_t の定義は `agenteval.econ.context_cost.verbose_attribution` と同じ（新しい定義は作らない）:
    D_t = 平均 Cost(処置, t) − 平均 Cost(基準, t)（直接計算の額）、
    N_t = 平均 CCA_tool(処置, t) − 平均 CCA_tool(基準, t)。使うタスク・乱数（同じ seed の `default_rng` で
    同じ形のタスク再標本）も同じなので、点推定の比は `verbose_attribution` の値と一致する。
    steps は run あたり平均ステップ数の差（タスクごとの 平均 n_steps(処置) − 平均 n_steps(基準) の、タスクの平均）。
    戻り値のキー: numerator_usd（Σ N_t）、denominator_usd（Σ D_t）、mean_steps_diff（タスクの平均）。
    各値は estimate・low・high（2.5%・97.5% のパーセンタイル）・n_tasks。
    """
    tasks = sorted(t for t in base_by_task if t in treated_by_task)
    tasks = [t for t in tasks if len(base_by_task[t]) > 0 and len(treated_by_task[t]) > 0]
    if not tasks:
        raise ValueError("基準と処置の両方に反復があるタスクが無い")

    def task_diff(f: Callable[[RunAllocation], float]) -> np.ndarray:
        return np.array(
            [
                float(np.mean([f(a) for a in treated_by_task[t]]))
                - float(np.mean([f(a) for a in base_by_task[t]]))
                for t in tasks
            ]
        )

    d = task_diff(lambda a: a.direct_usd)
    nn = task_diff(lambda a: cca_of_tool(a, tool))
    steps = task_diff(lambda a: float(a.n_steps))
    idx = np.random.default_rng(seed).integers(0, len(tasks), size=(n_boot, len(tasks)))

    def summarize(point: float, reps: np.ndarray) -> dict[str, float | int]:
        low, high = np.percentile(reps, [2.5, 97.5])
        return {"estimate": point, "low": float(low), "high": float(high), "n_tasks": len(tasks)}

    return {
        "numerator_usd": summarize(float(nn.sum()), nn[idx].sum(axis=1)),
        "denominator_usd": summarize(float(d.sum()), d[idx].sum(axis=1)),
        "mean_steps_diff": summarize(float(steps.mean()), steps[idx].mean(axis=1)),
    }


def compute(
    runs_by_version: Mapping[str, Sequence[Run]],
    ledger: Sequence[LedgerEntry],
    pricing: Pricing,
    control: tuple[Run, Run] | None,
    *,
    sub10: Sequence[str],
    h2_expected: Sequence[str] | None = None,
    seed: int,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:  # Any: 指標と補助量
    """指標を計算して {"metrics": …, "extras": …} を返す。

    対照 (a) の 2 run（`control`）は呼び出し側が作る（Docker が要る）。None なら control_attribution は None。
    """
    model = model_id(get_coding_version(BASE).model)
    prices = UnitPrices.from_pricing(pricing, model)
    all_runs = [r for v in VERSIONS for r in runs_by_version[v]]
    off_runs = list(runs_by_version[OFF])
    p_tokens = prefix_tokens_of(off_runs)
    allocs: dict[str, RunAllocation] = {
        r.run_id: allocate(r, prices, prefix_tokens=p_tokens) for r in all_runs
    }

    # --- H1: 恒等式 ---
    report = match_ledger(all_runs, ledger, modes=("live",))
    fully = {rid for rid, m in report.runs.items() if m.fully_matched}
    ident = [
        abs(allocs[rid].allocated_usd - report.runs[rid].ledger_usd) / report.runs[rid].ledger_usd
        for rid in fully
        if report.runs[rid].ledger_usd > 0.0
    ]
    ledger_usd = {rid: report.runs[rid].ledger_usd for rid in fully}
    # 空虚な合格を防ぐ: 照合できた run が少なすぎる（run 単位の対応率が EC-B2P の基準未満）か、検査した run が 0 本なら、
    # 恒等式の値を出さず（None）、基準を満たさない（FAIL）ことにする。基準そのもの（<= 0.001）は変えない
    voided = ""
    if not ident:
        voided = "恒等式を検査した run が 0 本"
    elif report.ledger_match_rate < MIN_LEDGER_MATCH_RATE:
        voided = f"台帳の照合率 {report.ledger_match_rate:.3f} が {MIN_LEDGER_MATCH_RATE} 未満"

    # --- H2: 詳細出力の run_tests への帰属 ---
    # H2 のタスク集合は c04 の run があるタスクから決める（ADR-044 (A): core24 × 3。ハードコードしない）。
    # c01 側はそのタスクの全反復（core24 では 6 反復）の平均を使う。H3（c01o は sub10 × 1）は sub10 のまま。
    sub = set(sub10)
    h2_tasks = {r.task_id for r in runs_by_version[VERBOSE]}
    if h2_expected is not None:
        # c04 か c01 にタスクの欠けがあると、積集合が縮んで分母・分子が黙って小さくなる。期待（corpus 計画の core24）と
        # 一致しなければ止める
        common = h2_tasks & {r.task_id for r in runs_by_version[BASE]}
        if common != set(h2_expected):
            raise ValueError(
                "H2 のタスク集合（c04 と c01 の積集合）が期待と一致しない: "
                f"欠け {sorted(set(h2_expected) - common)}、期待外 {sorted(common - set(h2_expected))}"
            )

    def by_task(version: str, tasks: set[str] | None = None) -> dict[str, list[RunAllocation]]:
        out: dict[str, list[RunAllocation]] = {}
        for r in runs_by_version[version]:
            if tasks is None or r.task_id in tasks:
                out.setdefault(r.task_id, []).append(allocs[r.run_id])
        return out

    attribution = verbose_attribution(
        by_task(BASE, h2_tasks), by_task(VERBOSE), tool=TOOL, n_boot=n_boot, seed=seed
    )
    # 比の分子・分母とステップ数の差（記録のみ。基準は付けない）。同じ N_t・D_t・タスク再標本を使うので比と整合する
    components = attribution_components(
        by_task(BASE, h2_tasks), by_task(VERBOSE), tool=TOOL, n_boot=n_boot, seed=seed
    )
    ratio_check = (
        components["numerator_usd"]["estimate"] / components["denominator_usd"]["estimate"]
    )
    if abs(ratio_check - attribution.value) > 1e-9 * max(1.0, abs(attribution.value)):
        raise ValueError(
            f"分子/分母 {ratio_check} が verbose_attribution の値 {attribution.value} と一致しない"
        )

    # --- H3: 履歴キャッシュを外したコスト増の予測（run 内の反実仮想） ---
    on_runs = [r for r in runs_by_version[BASE] if r.run_id in ledger_usd]
    off_ok = [r for r in off_runs if r.run_id in ledger_usd]
    # 台帳と対応した run が on・off のどちらかに無ければ予測誤差は出せない（None。基準を満たさない＝FAIL）
    pred = (
        history_cache_prediction_error(on_runs, off_ok, ledger_usd, prices, prefix_tokens=p_tokens)
        if on_runs and off_ok
        else None
    )
    on_sub = [r for r in on_runs if r.task_id in sub]
    pred_sub = (
        history_cache_prediction_error(on_sub, off_ok, ledger_usd, prices, prefix_tokens=p_tokens)
        if on_sub and off_ok
        else None
    )
    verbose_ok = [r for r in runs_by_version[VERBOSE] if r.run_id in ledger_usd]
    pred_c04 = (
        history_cache_prediction_error(
            verbose_ok, off_ok, ledger_usd, prices, prefix_tokens=p_tokens
        )
        if verbose_ok and off_ok
        else None
    )
    # 事前予測: 予測増加率 Σ Ŝ / Σ Ĉon（sub10 の c01 の run）
    s_hat = [
        counterfactual_cost(r, prices, history_cache=False, prefix_tokens=p_tokens)
        - counterfactual_cost(r, prices, history_cache=True, prefix_tokens=p_tokens)
        for r in on_sub
    ]
    c_on = [
        counterfactual_cost(r, prices, history_cache=True, prefix_tokens=p_tokens) for r in on_sub
    ]
    predicted_rate = float(sum(s_hat) / sum(c_on)) if on_sub and sum(c_on) > 0 else None

    # 版間のコスト増（c01o の run と sub10 の c01 の run。タスク単位のクラスタ・ブートストラップ。記録のみ）
    cost_on: dict[str, list[float]] = {}
    for r in runs_by_version[BASE]:
        if r.task_id in sub:
            cost_on.setdefault(r.task_id, []).append(r.cost.usd)
    cost_off = {r.task_id: r.cost.usd for r in off_runs}
    shared = sorted(set(cost_on) & set(cost_off))

    def increase_rate(tasks: Sequence[str]) -> float:
        on = float(np.mean([np.mean(cost_on[t]) for t in tasks]))
        off = float(np.mean([cost_off[t] for t in tasks]))
        return off / on - 1.0

    increase = (
        cluster_bootstrap(shared, increase_rate, n_boot=n_boot, seed=seed) if shared else None
    )

    # キャッシュ関係の切り分け: on の run で履歴部分の cache_read が 0 のステップがあるか、off の run の接頭辞より後ろに
    # cache_creation / cache_read があるか（FAIL の原因: 実装で history_cache が版に正しく入っていない）
    on_inactive = sum(
        history_cache_inactive(r, p_tokens) for r in runs_by_version[BASE] if len(r.steps) > 1
    )
    off_beyond = sum(
        any(
            s.usage.cache_read_input_tokens + s.usage.cache_creation_input_tokens > p_tokens
            for s in r.steps
        )
        for r in off_runs
    )

    # --- c12 − c01 の run_tests 帰属の差（記録のみ。区間付き） ---
    cca01 = by_task(BASE)
    cca12 = by_task(NULL)
    common12 = sorted(set(cca01) & set(cca12))

    def c12_minus_c01(tasks: Sequence[str]) -> float:
        return float(
            np.mean(
                [
                    np.mean([cca_of_tool(a, TOOL) for a in cca12[t]])
                    - np.mean([cca_of_tool(a, TOOL) for a in cca01[t]])
                    for t in tasks
                ]
            )
        )

    null_diff = (
        cluster_bootstrap(common12, c12_minus_c01, n_boot=n_boot, seed=seed) if common12 else None
    )

    # --- 対照 (a) ---
    control_ratio: float | None = None
    control_detail: dict[str, Any] | None = None
    if control is not None:
        early, late = control
        a_early, a_late = allocate(early, prices), allocate(late, prices)
        control_ratio = cca_ratio(a_late, LATE_STEP, a_early, EARLY_STEP)
        control_detail = {
            "early_run": early.run_id,
            "late_run": late.run_id,
            "ratio": control_ratio,
            "predicted": 1.0 / (LATE_STEP - EARLY_STEP + 1),  # n_L / n_E = 1/11
            "early_steps": len(early.steps),
            "late_steps": len(late.steps),
        }
    metrics: dict[str, Any] = {
        # --- 基準 ---
        "allocation_identity_error": labeled(
            max(a.allocation_identity_error for a in allocs.values()), "live"
        ),
        "ledger_identity_error": labeled(None if voided else max(ident), "live"),
        "verbose_attribution_to_run_tests": labeled(attribution.value, "live"),
        "history_cache_prediction_error": labeled(pred.error if pred else None, "live"),
        "control_attribution": labeled(control_ratio, "simulated"),
        # --- 記録のみ ---
        "verbose_attribution_ci": labeled(
            {
                "low": attribution.ci_low,
                "high": attribution.ci_high,
                "n_tasks": attribution.n_clusters,
            },
            "live",
        ),
        "verbose_attribution_numerator_usd": labeled(components["numerator_usd"], "live"),
        "verbose_attribution_denominator_usd": labeled(components["denominator_usd"], "live"),
        "c04_minus_c01_mean_steps": labeled(components["mean_steps_diff"], "live"),
        "e_on_e_off": labeled(
            {"e_on": pred.e_on, "e_off": pred.e_off, "n_on": pred.n_on, "n_off": pred.n_off}
            if pred
            else None,
            "live",
        ),
        "e_on_sub10_only": labeled(pred_sub.e_on if pred_sub else None, "live"),
        "e_on_c04": labeled(pred_c04.e_on if pred_c04 else None, "live"),
        "history_cache_predicted_increase_rate_sub10": labeled(predicted_rate, "live"),
        "history_cache_increase_between_versions": labeled(
            {"estimate": increase.estimate, "low": increase.low, "high": increase.high}
            if increase
            else None,
            "live",
        ),
        "history_cache_inactive_on_runs": labeled(on_inactive, "live"),
        "off_runs_with_cache_beyond_prefix": labeled(off_beyond, "live"),
        "prefix_tokens": labeled(p_tokens, "live"),
        "c12_minus_c01_run_tests_cca_usd": labeled(
            {"estimate": null_diff.estimate, "low": null_diff.low, "high": null_diff.high}
            if null_diff
            else None,
            "live",
        ),
        "ledger_match_rate": labeled(report.ledger_match_rate, "live"),
        "runs_fully_matched": labeled(len(fully), "live"),
        "ledger_identity_runs_checked": labeled(len(ident), "live"),
        "ledger_identity_voided_reason": labeled(voided or None, "live"),
        "runs_total": labeled(len(all_runs), "live"),
        "control_detail": labeled(control_detail, "simulated"),
        "predicted_resend_ratio_n10": labeled(predicted_resend_ratio(10, prices), "simulated"),
    }
    return {
        "metrics": json_safe(metrics),
        "extras": {
            "tolerance": LEDGER_IDENTITY_TOLERANCE,
            "cache_chain_broken": bool(on_inactive or off_beyond),
            "attribution": attribution.model_dump(),
        },
    }


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    """段 corpus の run と対照 (a) の run から指標を計算し、結果 JSON と結果ページを書く。

    `live` は何もしない（live の run は作らない）。対照 (a) の simulated run は、無ければこの実験が作る（Docker が要る）。
    """
    del live
    runs = require_stage_runs(STAGE, versions=VERSIONS)
    tasks = load_coding_tasks()
    by_version: dict[str, list[Run]] = {v: [] for v in VERSIONS}
    for run in runs:
        if run.version_id in by_version and tasks[run.task_id].kind == "bugfix":
            by_version[run.version_id].append(run)
    versions = load_coding_versions()
    same_tools = check_same_tool_definitions(versions, by_version)
    control = run_control_pair()
    out = compute(
        by_version,
        read_ledger(),
        load_pricing(),
        control,
        sub10=load_task_sets()["sub10"],
        h2_expected=load_task_sets()["core24"],  # corpus 計画の c04 の tasks（ADR-044 (A)）
        seed=seed,
    )
    metrics = out["metrics"]
    notes = [
        (
            "ledger_identity_error が検証するのは、単価表の写し・台帳の行との対応づけ・成分の総和だけで、ブロックごとの"
            "割り振り（どのツール結果にいくら掛かったか）の正しさは示さない。ブロック別の手がかりは位置モデルの前提と"
            "対照 (a) だけである（EC-B2P.md と同じ）。恒等式を検査した run は "
            f"{metrics['ledger_identity_runs_checked']['value']} 本（台帳の照合率が 0.95 未満か 0 本なら値を出さず FAIL にする。"
            f"理由: {metrics['ledger_identity_voided_reason']['value']}）。"
        ),
        (
            "対照 (a) は simulated（合成 usage。1 トークン = 4 バイトの近似）で、算術でほぼ決まる。確かめたのは"
            "「出力の大きさだけで帰属する実装（再送回数を見ない）は比が 1.0 になる」ことが排除されていること、"
            f"つまり CCA が生涯（再送回数）を反映していること。比 {metrics['control_attribution']['value']}"
            "（位置モデルの予測は 1/11 ≈ 0.091）。絶対額を live と並べない。"
        ),
        (
            "H2 は c04 の詳細出力が振る舞い（ステップ数）を変えうるので、コスト増 D_t に再送以外の増分も入る。"
            "比が 1 を超えることも 0.8 を下回ることもある。0.8 は「増分の大部分が run_tests 出力の生涯コスト」という"
            f"操作的な定義である。区間: {metrics['verbose_attribution_ci']['value']}。"
        ),
        (
            "H3 の主指標は run 内の反実仮想 |e_off − e_on|（キャッシュ関係の usage を予測に使わない）。情報の大半は e_on が"
            f"持つ。e_on/e_off: {metrics['e_on_e_off']['value']}。キャッシュの鎖の切断（履歴部分の cache_read が 0 の on の run "
            f"{metrics['history_cache_inactive_on_runs']['value']} 件、接頭辞より後ろにキャッシュのある off の run "
            f"{metrics['off_runs_with_cache_beyond_prefix']['value']} 件）が 0 でなければ実装の誤り（FAIL）として扱う。"
        ),
        (
            "c04 と c01 のツール定義・プロンプトの同一性を確かめた（接頭辞 P を共通に使う前提）: "
            f"{same_tools}。c12・c13 は system プロンプトが c01 と違うので c01o の P は使えず、版ごとの e_on は c04 だけを"
            "記録した（c12・c13 は「該当なし」）。c12 − c01 の run_tests 帰属の差は記録のみ（同等性の幅を決めていない）。"
        ),
    ]
    return finalize_coding(
        EXPERIMENT_ID,
        metrics,
        notes,
        seed=seed,
        provenance="live",
        method_fallback=METHOD_FALLBACK,
        # 自動の判定は FAIL。NEGATIVE は designer が --failure-type negative --reason で明示する
        caution=failure_caution(EXPERIMENT_ID),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
