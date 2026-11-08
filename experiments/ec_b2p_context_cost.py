"""EC-B2P コンテキスト・コスト帰属の試作 — 既存 live 軌跡での課金割り振りと台帳の照合。

仮説・植込み・対照・基準は `docs/results/EC-B2P.md`（正本）と registry.yaml の EC-B2P。
入力は既存の office 環境の live 軌跡（`data/traces/live__*.json`、240 本）と台帳（`data/cost_ledger.jsonl`）。
**新しい run を作らない**（API を呼ばない）。来歴は live（既存記録の再計算）。植込みの検出率だけ simulated。

台帳との照合は SPEC.md 10 節（K3）の完全一致規則 `econ.context_cost.match_ledger` を使う。
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _coding_common import finalize_coding, json_safe, labeled

from agenteval.core.schema import Run
from agenteval.core.store import TRACE_DIR
from agenteval.econ.context_cost import (
    LEDGER_IDENTITY_TOLERANCE,
    AllocationPlant,
    LedgerMatchReport,
    RunAllocation,
    UnitPrices,
    allocate,
    match_ledger,
    perturb_drop_tool_result,
    plant_eligible,
)
from agenteval.llm.cost import LedgerEntry, Pricing, load_pricing, read_ledger

EXPERIMENT_ID = "EC-B2P"
OFFLINE_SEED = 20261100  # coding/corpus.yaml の offline 段の seed。この実験に乱数は無い（決定的）

METHOD_FALLBACK = (
    "既存の office 環境の live 軌跡 240 本の `usage` から位置モデルで課金を割り振り、台帳（呼び出し時に "
    "`llm/cost.py` が独立に書いた実費）と (run_id, step, 4 種のトークン数) の完全一致で照合する。"
    "詳細は docs/results/EC-B2P.md。"
)


def dry_run(seed: int = OFFLINE_SEED) -> dict[str, Any]:  # Any: 表示用
    """`exp --dry-run`: 読む既存記録の有無。run は作らない（API を呼ばない。費用 0）。"""
    del seed
    from agenteval.llm.cost import LEDGER_PATH

    traces = sorted(TRACE_DIR.glob("live__*.json"))
    return {
        "creates_runs": 0,
        "reads": "既存の office 環境の live 軌跡（data/traces/live__*.json）と台帳（data/cost_ledger.jsonl の live の行）",
        "live_traces": len(traces),
        "ledger_exists": LEDGER_PATH.exists(),
        "ready": bool(traces) and LEDGER_PATH.exists(),
        "usd": 0.0,
    }


def load_live_traces(directory: Path | None = None) -> list[Run]:
    """`live__*.json` の軌跡を読む（mode が live のものだけ）。"""
    base = directory or TRACE_DIR
    runs = [
        Run.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(base.glob("live__*.json"))
    ]
    return [r for r in runs if r.mode == "live"]


def _model_by_run(ledger: Sequence[LedgerEntry]) -> dict[str, str]:
    """run_id → 台帳の行のモデル。同じ run_id の行が複数のモデルにまたがるときは None 扱い（含めない）。"""
    seen: dict[str, set[str]] = {}
    for entry in ledger:
        if entry.mode == "live":
            seen.setdefault(entry.run_id, set()).add(entry.model)
    return {run_id: next(iter(models)) for run_id, models in seen.items() if len(models) == 1}


def select_p4_index(run: Run, prices: UnitPrices) -> int | None:
    """植込み P4 の j の選び方: 適格な j のうち**最小**（EC-B2P の適格条件。データの合否は使わない）。

    j は 0..n−3。適格な j が無ければ None（その run には P4 を適用しない）。
    """
    for j in range(max(0, len(run.steps) - 2)):
        if plant_eligible(run, prices, "P4", j=j):
            return j
    return None


def compute_metrics(
    runs: Sequence[Run], ledger: Sequence[LedgerEntry], pricing: Pricing
) -> dict[str, Any]:  # Any: 指標と補助量の入れ物
    """指標を計算する（ファイルを読まない。単体テストできる）。

    返す辞書の `metrics` が registry の criteria と記録のみの指標、`extras` が結果ページの注記用の量。
    """
    report: LedgerMatchReport = match_ledger(runs, ledger, modes=("live",))
    models = _model_by_run(ledger)
    allocs: dict[str, RunAllocation] = {}
    prices_of: dict[str, UnitPrices] = {}
    no_model: list[str] = []
    for run in runs:
        model = models.get(run.run_id)
        if model is None:
            no_model.append(run.run_id)
            continue
        prices_of[run.run_id] = UnitPrices.from_pricing(pricing, model)
        allocs[run.run_id] = allocate(run, prices_of[run.run_id])

    # --- 自己検査と位置モデルの前提（モデルが分かった run） ---
    allocation_identity_error = max(
        (a.allocation_identity_error for a in allocs.values()), default=0.0
    )
    pairs = sum(max(0, a.n_steps - 1) for a in allocs.values())
    negative_pairs = sum(len(a.negative_increment_steps) for a in allocs.values())
    m4_negative = sum(len(a.negative_share_steps) for a in allocs.values())

    # --- 台帳との恒等式（全ステップが対応した run だけ） ---
    ident: dict[str, float] = {}
    naive: dict[str, float] = {}
    for run_id, match in report.runs.items():
        alloc = allocs.get(run_id)
        if alloc is None:
            continue
        if match.fully_matched and match.ledger_usd > 0.0:
            ident[run_id] = abs(alloc.allocated_usd - match.ledger_usd) / match.ledger_usd
        if match.naive_usd > 0.0:
            naive[run_id] = abs(alloc.allocated_usd - match.naive_usd) / match.naive_usd
    ledger_identity_error = max(ident.values(), default=0.0)

    # --- 植込み（全ステップが対応した run だけ。適格な組にだけ適用） ---
    cases: Counter[str] = Counter()
    detected: Counter[str] = Counter()
    p4_choice: Counter[int] = Counter()
    for run in runs:
        match = report.runs[run.run_id]
        if not match.fully_matched or match.ledger_usd <= 0.0 or run.run_id not in allocs:
            continue
        prices = prices_of[run.run_id]
        for plant, flags in (
            ("P2", AllocationPlant(drop_assistant_resend=True)),
            ("P3", AllocationPlant(cache_write_as_input=True)),
        ):
            if plant_eligible(run, prices, plant):  # type: ignore[arg-type]
                cases[plant] += 1
                planted = allocate(run, prices, plant=flags)
                error = abs(planted.allocated_usd - match.ledger_usd) / match.ledger_usd
                detected[plant] += int(error > LEDGER_IDENTITY_TOLERANCE)
        j = select_p4_index(run, prices)
        if j is not None:
            cases["P4"] += 1
            p4_choice[j] += 1
            planted_p4 = allocate(perturb_drop_tool_result(run, j), prices)
            detected["P4"] += int(len(planted_p4.negative_increment_steps) > 0)
    total_cases = sum(cases.values())
    detection_rate: float | None = sum(detected.values()) / total_cases if total_cases else None

    fully = [rid for rid, m in report.runs.items() if m.fully_matched]
    comp_keys = ("prefix_usd", "initial_user_usd", "assistant_resend_usd", "output_usd")
    components = {
        key: sum(getattr(allocs[r], key) for r in fully if r in allocs) for key in comp_keys
    }
    components["cca_usd"] = sum(sum(allocs[r].cca_usd.values()) for r in fully if r in allocs)
    char_gap = [
        abs(alloc.char_ratio_tool_tokens[k] - _tau(alloc, k))
        for alloc in allocs.values()
        for k in alloc.char_ratio_tool_tokens
    ]
    metrics: dict[str, Any] = {
        "ledger_identity_error": labeled(ledger_identity_error, "live"),
        "allocation_identity_error": labeled(allocation_identity_error, "live"),
        "negative_increment_rate": labeled(negative_pairs / pairs if pairs else 0.0, "live"),
        "ledger_match_rate": labeled(report.ledger_match_rate, "live"),
        "perturbation_detection_rate": labeled(detection_rate, "simulated"),
        # --- 記録のみ ---
        "m4_negative_share_rate": labeled(m4_negative / pairs if pairs else 0.0, "live"),
        "unmatched_ledger_rows": labeled(
            sum(m.unmatched_ledger_rows for m in report.runs.values()), "live"
        ),
        "duplicate_identical_rows": labeled(
            sum(m.duplicate_identical_rows for m in report.runs.values()), "live"
        ),
        "naive_ledger_identity_error": labeled(max(naive.values(), default=0.0), "live"),
        "step_match_rate": labeled(report.step_match_rate, "live"),
        "runs_total": labeled(len(runs), "live"),
        "runs_fully_matched": labeled(len(fully), "live"),
        "runs_without_model_in_ledger": labeled(len(no_model), "live"),
        "perturbation_cases": labeled(dict(sorted(cases.items())), "simulated"),
        "perturbation_detected": labeled(dict(sorted(detected.items())), "simulated"),
        "component_usd_fully_matched": labeled(
            {k: round(v, 6) for k, v in components.items()}, "live"
        ),
        "mean_abs_tau_minus_char_ratio_tokens": labeled(
            sum(char_gap) / len(char_gap) if char_gap else 0.0, "live"
        ),
    }
    return {
        "metrics": json_safe(metrics),
        "extras": {
            "n_runs": len(runs),
            "p4_j_counts": dict(sorted(p4_choice.items())),
            "ledger_identity_runs": len(ident),
            "naive_error_runs": len(naive),
            "max_ledger_error_run": max(ident, key=lambda r: ident[r]) if ident else None,
        },
    }


def _tau(alloc: RunAllocation, k: int) -> float:
    """M4 の残り τ_k = ツール結果のトークン数（セグメントから）。"""
    for seg in alloc.segments:
        if seg.seg_id == f"t{k}":
            return float(seg.tokens)
    return 0.0


def main(live: bool = False, seed: int = OFFLINE_SEED) -> dict[str, Any]:
    """既存の live 軌跡と台帳から指標を計算し、結果 JSON と結果ページを書く。

    `live` は何もしない（この実験は run を作らず、既存記録を再計算するだけ）。
    """
    del live
    runs = load_live_traces()
    if not runs:
        raise RuntimeError(
            "既存の live 軌跡（data/traces/live__*.json）が無い。EC-B2P は既存記録の再計算で、"
            "run を作らない。メインのチェックアウトの data/traces/ と data/cost_ledger.jsonl を用意する"
        )
    out = compute_metrics(runs, read_ledger(), load_pricing())
    metrics = out["metrics"]
    extras = out["extras"]
    cases = metrics["perturbation_cases"]["value"]
    notes = [
        (
            "`ledger_identity_error` が検証するのは、単価表の写し（UnitPrices）・台帳の行と trace のステップの"
            "対応づけ（K3 の完全一致規則）・成分の総和が usage の総額に戻ること、の 3 つだけである。台帳の usd は"
            "呼び出し時に llm/cost.py が同じ usage と同じ単価表から計算した値なので、ブロックごとの割り振り"
            "（どのツール結果にいくら掛かったか）の正しさは示さない。ブロック別の正しさの手がかりは位置モデルの前提"
            f"（negative_increment_rate）と EC-B2 の対照 (a) だけである。恒等式を検査した run は {extras['ledger_identity_runs']} 本。"
        ),
        (
            "植込みの検出率 1.0 は配管の煙試験であって検出感度ではない。適格条件（植込みの影響 ≥ 0.002、P4 は"
            "τ_j が最後の増分を上回る）と検出条件（恒等式の誤差 > 0.001、P4 は Δ < 0）が同じ量なので、適格な組での"
            f"検出はほぼ算術的に決まる。適格な組の数（perturbation_cases）: {cases}。"
        ),
        (
            "allocation_identity_error は定義上ほぼ 0 になる自己検査で、H1 の証拠にしない。情報があるのは台帳との"
            "一致（ledger_identity_error・ledger_match_rate）と位置モデルの前提（negative_increment_rate）の 3 つ。"
        ),
        (
            "台帳には同じ run_id の再実行の記録が残っている（K3）。unmatched_ledger_rows と duplicate_identical_rows は"
            "それを数えたもので、素朴な照合（run_id の全行の usd を足す）の恒等式の誤差は "
            f"naive_ledger_identity_error（{metrics['naive_ledger_identity_error']['value']}）に残した。"
            "これが 0.001 を大きく超えることが、完全一致の対応づけを使う理由である。"
        ),
        (
            "植込み P4 の j の選び方: 適格な j（τ_j > 0 かつ τ_j が最後の増分を上回り、元の run に負の増分が無い）の"
            f"うち最小を run ごとに選んだ（データの合否は使わない）。選ばれた j の度数: {extras['p4_j_counts']}。"
        ),
    ]
    return finalize_coding(
        EXPERIMENT_ID,
        metrics,
        notes,
        seed=seed,
        provenance="live",
        method_fallback=METHOD_FALLBACK,
        # 位置モデルの前提が崩れた場合（負の増分）は、実装は正しく手法の適用範囲が狭まる NEGATIVE
        negative_if_only=("negative_increment_rate",),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
