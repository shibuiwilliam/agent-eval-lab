"""章 C（コーディング環境）の実験スクリプトが共有する処理。

`_common.py`（既存の `finalize`・`labeled`）を土台に、次を足す。
- 結果ページの「方法」を、設計者が書いた結果ページ（docs/results/EC-*.md）から引き継ぐ。
  `finalize` は結果ページ全体を書き直すので、何もしないと設計者の「方法」が短い文字列で潰れる。
- live の実験（EC-B2・R1・R2）が段 `corpus` の run だけを読むための入口（run が無ければ案内付きで止まる）。
- 結果 JSON の来歴ラベル付けと、判定の種類（FAIL / NEGATIVE）の選択。
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from _common import finalize, labeled, meta_of
from numpy.typing import NDArray

from agenteval.econ.roi import ThetaSample, VersionEconomics
from agenteval.llm.cost import REPO_ROOT
from agenteval.reports import pages

__all__ = [
    "breakeven_summary",
    "conditional_rob",
    "design_method",
    "failing_metrics",
    "finalize_coding",
    "json_safe",
    "labeled",
    "nv_array",
]

_METHOD_RE = re.compile(r"^## 方法\n(.*?)\n## 結果", re.DOTALL | re.MULTILINE)


def design_method(experiment_id: str) -> str:
    """設計者が書いた結果ページの「方法」。無ければ空文字列（呼び出し側が代わりを渡す）。

    `finalize` が書く結果ページは `## 方法\\n{method}\\n\\n## 結果` の形なので、再生成しても同じ文字列に戻る
    （冪等）。
    """
    path = REPO_ROOT / "docs" / "results" / f"{experiment_id}.md"
    if not path.exists():
        return ""
    match = _METHOD_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else ""


def json_safe(value: Any) -> Any:  # Any: JSON にする任意の値
    """nan・inf を文字列にする（JSON に nan を書かない。区別が要る値は呼び出し側が理由を添える）。"""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [json_safe(v) for v in value]
    return value


def failing_metrics(experiment_id: str, metrics: Mapping[str, Any]) -> list[str]:
    """registry の criteria のうち満たしていない指標名。値が無い（None）ものも含む。"""
    out: list[str] = []
    for criterion in meta_of(experiment_id).get("criteria", []):
        value = metrics.get(criterion["metric"])
        if value is None or not pages.check_criterion(value, criterion):
            out.append(str(criterion["metric"]))
    return out


def finalize_coding(
    experiment_id: str,
    metrics: dict[str, Any],
    notes: list[str],
    *,
    seed: int,
    provenance: str,
    method_fallback: str = "",
    negative_if_only: Sequence[str] = (),
    figures: list[tuple[str, str]] | None = None,
    verdict_reason_extra: str = "",
) -> dict[str, Any]:
    """結果 JSON と結果ページを書く。

    `negative_if_only`: 未達の基準がこの指標の部分集合だけなら NEGATIVE（実装は正しいが手法が主張どおりに
    働かなかった）、それ以外の未達は FAIL（実装や設計の不備）。判断の根拠は各 EC-*.md の
    「FAIL と NEGATIVE の区別」。
    """
    failed = failing_metrics(experiment_id, metrics)
    failure_type = "fail"
    if failed and negative_if_only and set(failed) <= set(negative_if_only):
        failure_type = "negative"
    return finalize(
        experiment_id,
        metrics,
        method=design_method(experiment_id) or method_fallback,
        notes=notes,
        figures=figures,
        seed=seed,
        provenance=provenance,
        failure_type=failure_type,
        verdict_reason_extra=verdict_reason_extra,
    )


# ---------------------------------------------------------------------------
# Θ 上のベクトル化した計算（EC-R1・EC-R2 の記録のみの指標）
# ---------------------------------------------------------------------------

FloatArray = NDArray[np.float64]


def nv_array(e: VersionEconomics, thetas: ThetaSample) -> FloatArray:
    """NV = H − E[C_del] を Θ の全標本で計算する（`econ.roi.net_value` のベクトル版。単体テストで一致を確かめる）。"""
    a = thetas.arrays
    cost = (
        e.cost_usd
        + e.pi_S * (a.R0 + a.rho * e.diff_lines_S)
        + e.pi_F * (a.R_F + a.H_prime)
        + e.pi_W * (a.R0 + a.rho * e.diff_lines_W + a.r * a.H_prime + (1.0 - a.r) * a.D)
    )
    return np.asarray(a.H - cost, dtype=float)


def breakeven_summary(e: VersionEconomics, thetas: ThetaSample) -> dict[str, Any]:
    """損益分岐の成功率 π_S* の Θ 上の分布。nan の θ を「常に得」「常に損」「定義なし」に分けて割合を記録する。

    `econ.roi.breakeven_success_rate` は解が [0, 1] に無いと nan を返すので、nan の理由はここで再計算して分ける。
    - 常に得: π_S* < 0（π_S = 0 でも NV > 0）
    - 常に損: π_S* > 1（π_S = 1 でも NV < 0）
    - 定義なし: π_W + π_F = 0（比 q が決まらない）か、分母（混合費用 − S の費用）が 0 以下
    分位点は定義のある θ だけで計算し、除いた割合を併記する。
    """
    a = thetas.arrays
    n = len(a.H)
    tail = e.pi_W + e.pi_F
    if tail <= 0.0:
        return {
            "defined": 0.0,
            "always_gain": 0.0,
            "always_loss": 0.0,
            "undefined": 1.0,
            "quantiles": None,
        }
    q = e.pi_W / tail
    c_s = a.R0 + a.rho * e.diff_lines_S
    c_w = a.R0 + a.rho * e.diff_lines_W + a.r * a.H_prime + (1.0 - a.r) * a.D
    c_f = a.R_F + a.H_prime
    mix = q * c_w + (1.0 - q) * c_f
    denom = mix - c_s
    ok = denom > 0.0
    pi_s = np.where(ok, (mix + e.cost_usd - a.H) / np.where(ok, denom, 1.0), np.nan)
    gain = ok & (pi_s < 0.0)
    loss = ok & (pi_s > 1.0)
    defined = ok & (pi_s >= 0.0) & (pi_s <= 1.0)
    qs = (
        {
            f"q{int(p * 100):02d}": float(np.quantile(pi_s[defined], p))
            for p in (0.05, 0.25, 0.5, 0.75, 0.95)
        }
        if defined.any()
        else None
    )
    return {
        "defined": float(defined.sum() / n),
        "always_gain": float(gain.sum() / n),
        "always_loss": float(loss.sum() / n),
        "undefined": float((~ok).sum() / n),
        "quantiles": qs,
    }


def conditional_rob(
    nv_diff: FloatArray, thetas: ThetaSample, *, bins: int = 3
) -> dict[str, list[float]]:
    """各基底パラメータを `bins` 分位に分けたときの条件付き Rob（NV の差 > 0 の割合）。

    パラメータは H・kappa（H'/H）・R0・rho・R_F・D・miss（1 − r）。低い順の分位ごとの値を返す。
    """
    a = thetas.arrays
    columns: dict[str, FloatArray] = {
        "H": a.H,
        "kappa": a.H_prime / a.H,
        "R0": a.R0,
        "rho": a.rho,
        "R_F": a.R_F,
        "D": a.D,
        "miss": 1.0 - a.r,
    }
    positive = nv_diff > 0.0
    out: dict[str, list[float]] = {}
    for name, col in columns.items():
        edges = np.quantile(col, np.linspace(0.0, 1.0, bins + 1))
        idx = np.clip(np.searchsorted(edges, col, side="right") - 1, 0, bins - 1)
        out[name] = [float(positive[idx == b].mean()) for b in range(bins)]
    return out
