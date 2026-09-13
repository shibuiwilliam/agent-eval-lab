"""E0-1 シミュレート・エージェントと live の一致度（ADR-008 の妥当性検証）。

`docs/results/README.md` の拡張案 5 に当たる。sim で得た全結果の前提（sim は live の代替になる）を、
同じ条件の live 実行と突き合わせて確かめる。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.agent.loop import RunOptions, run_task
from agenteval.core.registry import get_task, get_version
from agenteval.core.schema import Run
from agenteval.core.store import save_run
from agenteval.process import metrics as metrics_mod
from agenteval.pts.selector import similarity
from agenteval.reports import plotting

TASKS = ["T-001", "T-002", "T-003", "T-005", "T-006", "T-015", "T-022", "T-028", "T-101", "T-201"]
VERSIONS = ["v01_baseline", "v03_noverify", "v07_step_minimizer"]
REPEATS = 3
SEED = 20260913

METHOD = """同じ (task, version, repeat) の組を live と sim の両方で実行し、評価手法が入力に使う量を
突き合わせた。live は `AGENTEVAL_LLM_MODE=record`（Haiku 4.5、プロンプトキャッシュ有効）、
sim は既存コーパスの同じ組。対応づけは (task, version, repeat) の完全一致で行い、版をまたいだ比較はしない。
比較する量:
  - `outcome.passed` の一致率（同じ組で合否が一致した割合）
  - 版ごとの合格率の差（sim が live の水準をどれだけずらすか）
  - ツール名列の類似度（正規化編集距離の補数。`pts/selector.py` と同じ定義）
  - 版の順序が保たれるか（合格率の大小関係が live と sim で同じか）
この実験だけは来歴が `live`（live と sim の**比較**なので、両方の数値が出てくる。
表では列で分けており、混ぜた統計量は作っていない）。"""


def live_run(task_id: str, version_id: str, repeat: int) -> Run:
    run = run_task(
        get_task(task_id),
        get_version(version_id),
        RunOptions(
            mode="record",
            seed=SEED,
            repeat=repeat,
            keep_snapshots=False,
            run_id_override=f"live__{task_id}__{version_id}__r{repeat}",
        ),
    )
    save_run(run)
    return run


def main(live: bool = False, seed: int = SEED) -> dict[str, Any]:
    sim_runs = {
        (r.task_id, r.version_id, r.repeat): r
        for r in corpus()
        if r.task_id in TASKS and r.version_id in VERSIONS and r.repeat < REPEATS
    }

    live_runs: dict[tuple[str, str, int], Run] = {}
    if live:
        for version_id in VERSIONS:
            for task_id in TASKS:
                for repeat in range(REPEATS):
                    live_runs[(task_id, version_id, repeat)] = live_run(task_id, version_id, repeat)
    else:
        from agenteval.core.store import TRACE_DIR, load_run

        for path in sorted(TRACE_DIR.glob("live__*.json")):
            run = load_run(path.stem)
            live_runs[(run.task_id, run.version_id, run.repeat)] = run

    keys = sorted(set(sim_runs) & set(live_runs))
    if not keys:
        raise RuntimeError("live の run がありません。`--live` を付けて実行してください")

    agree = [sim_runs[k].passed() == live_runs[k].passed() for k in keys]
    sims = [similarity(live_runs[k].tool_names(), sim_runs[k].tool_names()) for k in keys]

    def rate(runs: dict[tuple[str, str, int], Run], version_id: str) -> float:
        subset = [r for k, r in runs.items() if k[1] == version_id and k in keys]
        return sum(r.passed() for r in subset) / len(subset) if subset else 0.0

    live_rates = {v: rate(live_runs, v) for v in VERSIONS}
    sim_rates = {v: rate(sim_runs, v) for v in VERSIONS}
    gap = max(abs(live_rates[v] - sim_rates[v]) for v in VERSIONS)
    order_live = sorted(VERSIONS, key=lambda v: live_rates[v], reverse=True)
    order_sim = sorted(VERSIONS, key=lambda v: sim_rates[v], reverse=True)

    def metric(runs: dict[tuple[str, str, int], Run], field: str) -> float:
        rows = [metrics_mod.compute(runs[k], l_min=get_task(k[0]).l_min) for k in keys]
        return metrics_mod.aggregate(rows, field)

    fig = plotting.bar_compare(
        "E0-1",
        "pass_rate",
        "版ごとの合格率（live と sim）",
        [f"{v}\nlive" for v in VERSIONS] + [f"{v}\nsim" for v in VERSIONS],
        [live_rates[v] for v in VERSIONS] + [sim_rates[v] for v in VERSIONS],
        "合格率",
        "live vs simulated",
    )

    usd = sum(r.cost.usd for r in live_runs.values())
    metrics = {
        "outcome_agreement": labeled(round(float(np.mean(agree)), 4), "live"),
        "pass_rate_gap": labeled(round(gap, 4), "live"),
        "tool_sequence_similarity": labeled(round(float(np.mean(sims)), 4), "live"),
        "version_ordering_preserved": labeled(int(order_live == order_sim), "live"),
        "n_pairs": labeled(len(keys), "live"),
        "live_pass_rate_v01": labeled(round(live_rates["v01_baseline"], 4), "live"),
        "sim_pass_rate_v01": labeled(round(sim_rates["v01_baseline"], 4), "simulated"),
        "live_steps": labeled(round(metric(live_runs, "steps"), 4), "live"),
        "sim_steps": labeled(round(metric(sim_runs, "steps"), 4), "simulated"),
        "live_verification_rate": labeled(round(metric(live_runs, "verification_rate"), 4), "live"),
        "sim_verification_rate": labeled(
            round(metric(sim_runs, "verification_rate"), 4), "simulated"
        ),
        "live_dup_rate": labeled(round(metric(live_runs, "dup_rate"), 4), "live"),
        "sim_dup_rate": labeled(round(metric(sim_runs, "dup_rate"), 4), "simulated"),
        "live_stop_appropriate": labeled(round(metric(live_runs, "stop_appropriate"), 4), "live"),
        "sim_stop_appropriate": labeled(
            round(metric(sim_runs, "stop_appropriate"), 4), "simulated"
        ),
        "live_usd": labeled(round(usd, 4), "live"),
    }
    notes = [
        f"live の版ごとの合格率: { {k: round(v, 3) for k, v in live_rates.items()} }",
        f"sim の版ごとの合格率: { {k: round(v, 3) for k, v in sim_rates.items()} }",
        f"合格率の順序 live={order_live} / sim={order_sim}",
        (
            "sim は (task, seed, repeat) に対して決定的なので、live の 1 回の実行と比べている。"
            "live 側の揺れ（同じ条件でも応答が変わる）は反復 3 回ぶんしか見ていない。"
        ),
    ]
    return finalize(
        "E0-1",
        metrics,
        METHOD,
        notes,
        figures=[("合格率の比較", fig)],
        seed=seed,
        provenance="live",
        usd=round(usd, 4),
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(live="--live" in sys.argv), ensure_ascii=False, indent=2))
