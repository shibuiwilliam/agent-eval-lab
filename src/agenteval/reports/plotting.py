"""図の共通設定。matplotlib のみ。DPI 150、日本語フォント。"""

from __future__ import annotations

from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from agenteval.llm.cost import REPO_ROOT

FIG_DIR = REPO_ROOT / "docs" / "results" / "fig"
CANDIDATE_FONTS = (
    "Hiragino Sans",
    "Hiragino Kaku Gothic ProN",
    "BIZ UDGothic",
    "AppleGothic",
    "Arial Unicode MS",
)


def setup() -> str:
    """日本語が化けないフォントを選ぶ。見つからなければ既定のまま。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in CANDIDATE_FONTS:
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["figure.dpi"] = 150
    plt.rcParams["savefig.dpi"] = 150
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    return str(plt.rcParams["font.family"])


def save(fig: Any, experiment_id: str, name: str) -> str:
    """`docs/results/fig/EX-Y_<name>.png` に保存し、相対パスを返す。"""
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / f"{experiment_id}_{name}.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return f"fig/{path.name}"


def new_axes(
    title: str, xlabel: str, ylabel: str, figsize: tuple[float, float] = (6.4, 4.0)
) -> tuple[Any, Any]:
    """1 図 1 主張の軸を作る。"""
    setup()
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return fig, ax


def bar_compare(
    experiment_id: str,
    name: str,
    title: str,
    labels: list[str],
    values: list[float],
    ylabel: str,
    provenance: str,
    threshold: float | None = None,
) -> str:
    """版やグループの比較棒グラフ。凡例に来歴ラベルを入れる。"""
    fig, ax = new_axes(title, "", ylabel)
    bars = ax.bar(labels, values, color="#4878a8", label=f"{provenance} (n={len(values)})")
    if threshold is not None:
        ax.axhline(threshold, color="#c04040", linestyle="--", label=f"基準 {threshold}")
    for rect, value in zip(bars, values, strict=True):
        ax.annotate(
            f"{value:.3g}",
            (rect.get_x() + rect.get_width() / 2, rect.get_height()),
            ha="center",
            va="bottom",
            fontsize=8,
        )
    ax.legend(fontsize=8)
    return save(fig, experiment_id, name)


def line_curve(
    experiment_id: str,
    name: str,
    title: str,
    xlabel: str,
    ylabel: str,
    series: dict[str, tuple[list[float], list[float]]],
    provenance: str,
) -> str:
    """複数系列の折れ線。凡例に来歴ラベルを入れる。"""
    fig, ax = new_axes(title, xlabel, ylabel)
    for label, (xs, ys) in series.items():
        ax.plot(xs, ys, marker="o", label=f"{label} [{provenance}]")
    ax.legend(fontsize=8)
    return save(fig, experiment_id, name)


def scatter(
    experiment_id: str,
    name: str,
    title: str,
    xlabel: str,
    ylabel: str,
    xs: list[float],
    ys: list[float],
    provenance: str,
    diagonal: bool = False,
) -> str:
    """散布図。"""
    fig, ax = new_axes(title, xlabel, ylabel)
    ax.scatter(xs, ys, color="#4878a8", label=f"{provenance} (n={len(xs)})")
    if diagonal:
        lo = min([*xs, *ys])
        hi = max([*xs, *ys])
        ax.plot([lo, hi], [lo, hi], color="#888", linestyle="--", label="y = x")
    ax.legend(fontsize=8)
    return save(fig, experiment_id, name)
