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


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    haiku_a = synthetic_fingerprint(MODEL_IDS["agent"], "haiku_a", seed)
    haiku_b = synthetic_fingerprint(MODEL_IDS["agent"], "haiku_b", seed + 1)
    sonnet = synthetic_fingerprint(MODEL_IDS["judge"], "sonnet", seed + 2)
    for f in (haiku_a, haiku_b, sonnet):
        f.save(Path("data/fingerprints"))

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
    }
    notes = [
        (
            "プローブ応答は代替生成器で作っている。Haiku 役は平均 40 文字、Sonnet 役は平均 90 文字、"
            "語頭の言い回しとツール選択の分布も変えてある。つまり「差がある 2 群」を人工的に作って"
            "検定にかけているので、入替が検知できるのは構造的に当然である。"
        ),
        (
            "この実験で本当に確かめたのは (1) 対照（同一分布の 2 群）で誤警報が出ないこと、"
            "(2) 順列検定の p 値が実装どおりに計算されること、の 2 点である。"
            "実モデルの無断更新を検知できるかは、live でベースライン指紋を取らないと分からない。"
            "P0 で live の指紋を取る計画だったが、API キーが無いため未実施。"
        ),
    ]
    return finalize("E8-6", metrics, METHOD, notes, figures=[("指紋の距離", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
