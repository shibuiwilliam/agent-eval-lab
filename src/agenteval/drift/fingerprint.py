"""モデル指紋（原典 8.8）。固定プローブの応答分布を比較して変化を検知する。

「無断更新」の代替はモデル入替（Haiku ↔ Sonnet）で行う。本物の無断更新は待てない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from agenteval.llm.cost import REPO_ROOT

PROBE_PATH = REPO_ROOT / "data" / "fixtures" / "probes.yaml"
FINGERPRINT_DIR = REPO_ROOT / "data" / "fingerprints"


@dataclass
class Fingerprint:
    """1 回の指紋取得。"""

    model: str
    label: str
    lengths: list[int] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    tool_choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def save(self, directory: Path | None = None) -> Path:
        target = (directory or FINGERPRINT_DIR) / f"{self.label}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, label: str, directory: Path | None = None) -> Fingerprint:
        path = (directory or FINGERPRINT_DIR) / f"{label}.json"
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def load_probes(path: Path | None = None) -> list[dict[str, Any]]:
    """固定プローブ 30 問。"""
    target = path or PROBE_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    probes: list[dict[str, Any]] = data["probes"]
    return probes


def collect_live(model: str, label: str, client: Any, chars: int = 20) -> Fingerprint:
    """実 API で指紋を取る（IMPROVEMENT.md R2）。

    プローブ 30 問はツール無しの短答、ミニタスク 10 問はツールを渡して
    「最初に選ぶツール」を記録する。ツール定義は毎回同じなので `cache_control` を付けて
    キャッシュに乗せる（コストを抑えるため）。
    """
    from agenteval.env.tools import tool_schemas
    from agenteval.llm.client import MessageRequest

    data = yaml.safe_load(PROBE_PATH.read_text(encoding="utf-8"))
    probes: list[dict[str, Any]] = data["probes"]
    mini: list[dict[str, Any]] = data["mini_tasks"]

    lengths: list[int] = []
    prefixes: list[str] = []
    for probe in probes:
        result = client.create(
            MessageRequest(
                model=model,
                system=[{"type": "text", "text": "短く答えてください。"}],
                messages=[{"role": "user", "content": [{"type": "text", "text": probe["prompt"]}]}],
                max_tokens=128,
            )
        )
        text = result.text().strip()
        lengths.append(len(text))
        prefixes.append(text[:chars])

    schemas = tool_schemas("v1")
    schemas[-1] = {**schemas[-1], "cache_control": {"type": "ephemeral"}}
    tool_choices: list[str] = []
    for task in mini:
        result = client.create(
            MessageRequest(
                model=model,
                system=[
                    {
                        "type": "text",
                        "text": "業務アシスタントとして、次の状況で最初に呼ぶべきツールを 1 つだけ呼んでください。",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": [{"type": "text", "text": task["prompt"]}]}],
                tools=schemas,
                max_tokens=256,
            )
        )
        uses = result.tool_uses()
        tool_choices.append(str(uses[0]["name"]) if uses else "__no_tool__")

    return Fingerprint(
        model=model, label=label, lengths=lengths, prefixes=prefixes, tool_choices=tool_choices
    )


def prefix_match_rate(a: Fingerprint, b: Fingerprint, chars: int = 20) -> float:
    """先頭 20 文字の一致率。"""
    pairs = list(zip(a.prefixes, b.prefixes, strict=False))
    if not pairs:
        return 0.0
    return round(sum(1 for x, y in pairs if x[:chars] == y[:chars]) / len(pairs), 4)


def statistic(a: Fingerprint, b: Fingerprint) -> float:
    """指紋間の距離。応答長の平均差 ＋ 先頭一致率の補数 ＋ ツール選択分布の差。"""
    len_diff = abs(float(np.mean(a.lengths or [0])) - float(np.mean(b.lengths or [0])))
    len_scale = max(1.0, float(np.mean((a.lengths or [1]) + (b.lengths or [1]))))
    prefix_term = 1.0 - prefix_match_rate(a, b)
    tools = sorted(set(a.tool_choices) | set(b.tool_choices))
    pa = np.array([a.tool_choices.count(t) for t in tools], dtype=float)
    pb = np.array([b.tool_choices.count(t) for t in tools], dtype=float)
    pa = pa / pa.sum() if pa.sum() else pa
    pb = pb / pb.sum() if pb.sum() else pb
    tool_term = float(np.abs(pa - pb).sum() / 2) if tools else 0.0
    return round(len_diff / len_scale + prefix_term + tool_term, 6)


def length_statistic(a: Fingerprint, b: Fingerprint) -> float:
    """応答長分布の平均差だけを見る統計量（ADR-013）。

    `statistic()` は応答長・先頭一致率・ツール選択分布を足し合わせるが、live の実測では
    後ろ 2 項が識別力を持たなかった:
      - 先頭 20 文字の一致率は同一モデル 2 回でも 0.2、別モデルでも 0.1 とほぼ同じ
      - ミニタスクのツール選択は 10 問中 8 問が `__no_tool__`（実モデルは文章で答える）に潰れる
    帰無仮説と対立仮説で同じ値を取る項は、順列検定の統計量に入れても信号を薄めるだけである。
    """
    return round(abs(float(np.mean(a.lengths or [0])) - float(np.mean(b.lengths or [0]))), 6)


def permutation_test_lengths(
    a: Fingerprint, b: Fingerprint, n_permutations: int = 2000, seed: int = 20260913
) -> dict[str, float]:
    """応答長だけの順列検定（ADR-013 の既定）。"""
    rng = np.random.default_rng(seed)
    observed = length_statistic(a, b)
    pooled = np.array(a.lengths + b.lengths, dtype=float)
    n = len(a.lengths)
    count = 0
    for _ in range(n_permutations):
        order = rng.permutation(len(pooled))
        left, right = pooled[order[:n]], pooled[order[n:]]
        if abs(float(np.mean(left)) - float(np.mean(right))) >= observed:
            count += 1
    return {"statistic": observed, "p_value": round((count + 1) / (n_permutations + 1), 4)}


def permutation_test(
    a: Fingerprint, b: Fingerprint, n_permutations: int = 1000, seed: int = 20260913
) -> dict[str, float]:
    """順列検定。プローブ応答（長さ・先頭）とミニタスクのツール選択を別々に並べ替える。

    2 つの系列は長さが違う（プローブ 30 問、ミニタスク 10 問）ので、同じ順列を使い回さない。
    """
    rng = np.random.default_rng(seed)
    observed = statistic(a, b)
    pooled_len = a.lengths + b.lengths
    pooled_prefix = a.prefixes + b.prefixes
    pooled_tools = a.tool_choices + b.tool_choices
    n_probe = len(a.lengths)
    n_tool = len(a.tool_choices)
    count = 0
    for _ in range(n_permutations):
        probe_order = rng.permutation(len(pooled_len))
        tool_order = rng.permutation(len(pooled_tools)) if pooled_tools else np.array([], dtype=int)
        left = Fingerprint(
            model="perm",
            label="left",
            lengths=[pooled_len[i] for i in probe_order[:n_probe]],
            prefixes=[pooled_prefix[i] for i in probe_order[:n_probe]],
            tool_choices=[pooled_tools[i] for i in tool_order[:n_tool]],
        )
        right = Fingerprint(
            model="perm",
            label="right",
            lengths=[pooled_len[i] for i in probe_order[n_probe:]],
            prefixes=[pooled_prefix[i] for i in probe_order[n_probe:]],
            tool_choices=[pooled_tools[i] for i in tool_order[n_tool:]],
        )
        if statistic(left, right) >= observed:
            count += 1
    return {"statistic": observed, "p_value": round((count + 1) / (n_permutations + 1), 4)}
