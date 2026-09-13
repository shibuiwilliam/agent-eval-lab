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


def permutation_test(
    a: Fingerprint, b: Fingerprint, n_permutations: int = 1000, seed: int = 20260913
) -> dict[str, float]:
    """順列検定。応答長を入れ替えて帰無分布を作る。"""
    rng = np.random.default_rng(seed)
    observed = statistic(a, b)
    pooled_len = np.array(a.lengths + b.lengths, dtype=float)
    pooled_prefix = a.prefixes + b.prefixes
    pooled_tools = a.tool_choices + b.tool_choices
    n = len(a.lengths)
    count = 0
    for _ in range(n_permutations):
        order = rng.permutation(len(pooled_len))
        left = Fingerprint(
            model="perm",
            label="left",
            lengths=[int(pooled_len[i]) for i in order[:n]],
            prefixes=[pooled_prefix[i] for i in order[:n]],
            tool_choices=[pooled_tools[i] for i in order[:n]] if pooled_tools else [],
        )
        right = Fingerprint(
            model="perm",
            label="right",
            lengths=[int(pooled_len[i]) for i in order[n:]],
            prefixes=[pooled_prefix[i] for i in order[n:]],
            tool_choices=[pooled_tools[i] for i in order[n:]] if pooled_tools else [],
        )
        if statistic(left, right) >= observed:
            count += 1
    return {"statistic": observed, "p_value": round((count + 1) / (n_permutations + 1), 4)}
