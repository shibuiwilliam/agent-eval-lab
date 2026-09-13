"""E8-6 モデル指紋による入替検知（原典 8.8）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.drift import fingerprint as fp_mod
from agenteval.llm.models import MODEL_IDS
from agenteval.reports import plotting

N_BOOTSTRAP = 20

METHOD = """`data/fixtures/probes.yaml` の固定 30 問（ツール無し・短答）と 10 個のミニタスクを使い、
応答長分布・先頭 20 文字の一致率・ツール選択分布から指紋を取った（原典 8.8）。
基準分布は同一モデルで 2 回取った差を帰無とし、検知は順列検定で p < 0.05。
対照（同一モデルの 2 回取得）の誤警報率はブートストラップ 20 回で測った。
**重要**: live API が使えないため、プローブ応答は決定的な代替生成器で作っている。
モデルごとに応答長と語尾の分布を変えた生成器であり、実際の Haiku / Sonnet の応答ではない。
したがってこの実験が検証したのは「指紋の統計量と順列検定の実装が、分布の違いに反応するか」までで、
実モデルの入替を検知できるかは検証していない。"""


def synthetic_fingerprint(model: str, label: str, seed: int) -> fp_mod.Fingerprint:
    """プローブ応答の代替生成器。モデルごとに応答の長さと言い回しの分布を変える。"""
    probes = fp_mod.load_probes()
    rng = np.random.default_rng(seed)
    is_sonnet = model == MODEL_IDS["judge"]
    mean_len = 90 if is_sonnet else 40
    prefixes_pool = (
        ["はい、", "結論から言うと", "こちらです。", "answer:"]
        if is_sonnet
        else ["", "答え:", "はい、"]
    )
    lengths = [int(max(5, rng.normal(mean_len, mean_len * 0.25))) for _ in probes]
    prefixes = [prefixes_pool[int(rng.integers(0, len(prefixes_pool)))] + p["id"] for p in probes]
    tools_pool = (
        ["calendar_search", "file_read", "checks_run", "mail_search"]
        if is_sonnet
        else ["calendar_search", "file_read", "finish"]
    )
    tool_choices = [tools_pool[int(rng.integers(0, len(tools_pool)))] for _ in range(10)]
    return fp_mod.Fingerprint(
        model=model, label=label, lengths=lengths, prefixes=prefixes, tool_choices=tool_choices
    )


def load_or_collect_live(seed: int) -> tuple[Any, Any, Any] | None:
    """live で取った指紋があれば読む。無ければ None（`--live` で取得する）。"""
    directory = Path("data/fingerprints")
    try:
        return (
            fp_mod.Fingerprint.load("live_haiku_a", directory),
            fp_mod.Fingerprint.load("live_haiku_b", directory),
            fp_mod.Fingerprint.load("live_sonnet", directory),
        )
    except FileNotFoundError:
        return None


def collect_live_fingerprints() -> tuple[Any, Any, Any]:
    """実 API で指紋を 3 本取る（Haiku ×2 が対照、Sonnet が入替）。"""
    from agenteval.llm.client import LLMClient

    client = LLMClient(mode="record", run_id="E8-6")
    directory = Path("data/fingerprints")
    out = []
    for model, label in (
        (MODEL_IDS["agent"], "live_haiku_a"),
        (MODEL_IDS["agent"], "live_haiku_b"),
        (MODEL_IDS["judge"], "live_sonnet"),
    ):
        fingerprint = fp_mod.collect_live(model, label, client)
        fingerprint.save(directory)
        out.append(fingerprint)
    return (out[0], out[1], out[2])


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    haiku_a = synthetic_fingerprint(MODEL_IDS["agent"], "haiku_a", seed)
    haiku_b = synthetic_fingerprint(MODEL_IDS["agent"], "haiku_b", seed + 1)
    sonnet = synthetic_fingerprint(MODEL_IDS["judge"], "sonnet", seed + 2)
    for f in (haiku_a, haiku_b, sonnet):
        f.save(Path("data/fingerprints"))

    live_set = collect_live_fingerprints() if live else load_or_collect_live(seed)

    control = fp_mod.permutation_test(haiku_a, haiku_b, n_permutations=500, seed=seed)
    swap = fp_mod.permutation_test(haiku_a, sonnet, n_permutations=500, seed=seed)

    false_alarms = []
    for i in range(N_BOOTSTRAP):
        a = synthetic_fingerprint(MODEL_IDS["agent"], f"a{i}", seed + 100 + i)
        b = synthetic_fingerprint(MODEL_IDS["agent"], f"b{i}", seed + 200 + i)
        false_alarms.append(
            fp_mod.permutation_test(a, b, n_permutations=200, seed=seed + i).get("p_value", 1.0)
            < 0.05
        )
    false_alarm_rate = float(np.mean(false_alarms))

    live_metrics: dict[str, Any] = {}
    live_notes: list[str] = []
    if live_set is not None:
        la, lb, ls = live_set
        # ADR-013: live の判定は応答長だけの順列検定を使う（識別力の無い項を外す）
        live_control = fp_mod.permutation_test_lengths(la, lb, n_permutations=2000, seed=seed)
        live_swap = fp_mod.permutation_test_lengths(la, ls, n_permutations=2000, seed=seed)
        # 改訂前の合成統計量も残す
        old_control = fp_mod.permutation_test(la, lb, n_permutations=500, seed=seed)
        old_swap = fp_mod.permutation_test(la, ls, n_permutations=500, seed=seed)
        live_metrics = {
            "live_control_p_value": labeled(live_control["p_value"], "live"),
            "live_swap_p_value": labeled(live_swap["p_value"], "live"),
            "live_control_statistic": labeled(live_control["statistic"], "live"),
            "live_swap_statistic": labeled(live_swap["statistic"], "live"),
            "live_haiku_mean_len": labeled(round(float(np.mean(la.lengths)), 1), "live"),
            "live_sonnet_mean_len": labeled(round(float(np.mean(ls.lengths)), 1), "live"),
            "live_prefix_match_control": labeled(fp_mod.prefix_match_rate(la, lb), "live"),
            "live_prefix_match_swap": labeled(fp_mod.prefix_match_rate(la, ls), "live"),
            "live_control_p_value_composite": labeled(old_control["p_value"], "live"),
            "live_swap_p_value_composite": labeled(old_swap["p_value"], "live"),
            "live_no_tool_rate": labeled(
                round(
                    sum(1 for t in la.tool_choices if t == "__no_tool__") / len(la.tool_choices), 4
                ),
                "live",
            ),
        }
        live_notes = [
            (
                "**live 指紋（IMPROVEMENT.md R2）**: 実 API で Haiku 4.5 を 2 回、Sonnet 5 を 1 回取得した"
                "（プローブ 30 問 ＋ ミニタスク 10 問 × 3 回 = 120 呼び出し）。"
                f"応答長の平均は Haiku {round(float(np.mean(la.lengths)), 1)} 文字、"
                f"Sonnet {round(float(np.mean(ls.lengths)), 1)} 文字。"
            ),
            (
                "**改訂前の合成統計量では入替を検知できなかった**: "
                f"対照 p = {old_control['p_value']}、入替 p = {old_swap['p_value']}（0.05 を超える）。"
                "人工分布では p = 0.002 で検知できていたので、これは人工分布に助けられていた結果だった。"
            ),
            (
                "ADR-013 で統計量から識別力の無い項（先頭一致率・ツール選択分布）を外し、"
                f"応答長だけの順列検定にすると 対照 p = {live_control['p_value']}、"
                f"入替 p = {live_swap['p_value']} と分離する。"
                "先頭一致率は同一モデル 2 回でも 0.2、別モデルでも 0.1 とほぼ差が無く、"
                f"ミニタスクのツール選択は {round(sum(1 for t in la.tool_choices if t == '__no_tool__') / len(la.tool_choices) * 100)}% が "
                "`__no_tool__`（実モデルは文章で答える）に潰れていた。"
            ),
            (
                "ただし入替の p はプローブ 30 問では 0.05 ぎりぎりである。"
                "プローブ数を増やすか、確実にツールを呼ばせるミニタスクにしないと安定しない。"
            ),
        ]

    fig = plotting.bar_compare(
        "E8-6",
        "statistic",
        "指紋の距離統計量（同一モデル vs モデル入替）",
        ["Haiku 2 回（対照）", "Haiku → Sonnet（入替）"],
        [control["statistic"], swap["statistic"]],
        "距離統計量",
        "simulated",
    )

    metrics = {
        "control_false_alarm_rate": labeled(round(false_alarm_rate, 4)),
        "swap_p_value": labeled(swap["p_value"]),
        "control_p_value": labeled(control["p_value"]),
        "swap_statistic": labeled(swap["statistic"]),
        "control_statistic": labeled(control["statistic"]),
        "n_probes": labeled(len(fp_mod.load_probes())),
        "n_bootstrap": labeled(N_BOOTSTRAP),
        **live_metrics,
    }
    notes = [
        *live_notes,
        (
            "プローブ応答は代替生成器で作っている。Haiku 役は平均 40 文字、Sonnet 役は平均 90 文字、"
            "語頭の言い回しとツール選択の分布も変えてある。つまり「差がある 2 群」を人工的に作って"
            "検定にかけているので、入替が検知できるのは構造的に当然である。"
        ),
        (
            "この実験で本当に確かめたのは (1) 対照（同一分布の 2 群）で誤警報が出ないこと、"
            "(2) 順列検定の p 値が実装どおりに計算されること、の 2 点である。"
            "実モデルの無断更新を検知できるかは、live でベースライン指紋を取らないと分からない。"
        ),
    ]
    return finalize("E8-6", metrics, METHOD, notes, figures=[("指紋の距離", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
