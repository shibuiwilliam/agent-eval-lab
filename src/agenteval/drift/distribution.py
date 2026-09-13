"""鮮度と分布距離（原典 8.3）。カテゴリ分布の JS ダイバージェンスと埋め込み MMD。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np


@dataclass
class MMDResult:
    """MMD の検定結果。"""

    mmd: float
    p_value: float
    n_permutations: int


def js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon ダイバージェンス（底 2、0〜1）。"""
    keys = sorted(set(p) | set(q))
    pv = np.array([p.get(k, 0.0) for k in keys], dtype=float)
    qv = np.array([q.get(k, 0.0) for k in keys], dtype=float)
    pv = pv / pv.sum() if pv.sum() else pv
    qv = qv / qv.sum() if qv.sum() else qv
    m = (pv + qv) / 2

    def kl(a: np.ndarray, b: np.ndarray) -> float:
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / np.where(b[mask] > 0, b[mask], 1e-12))))

    return round(0.5 * kl(pv, m) + 0.5 * kl(qv, m), 6)


def suite_distribution(categories: list[str]) -> dict[str, float]:
    """スイートのカテゴリ分布。"""
    counts: dict[str, int] = {}
    for category in categories:
        counts[category] = counts.get(category, 0) + 1
    total = sum(counts.values())
    return {k: v / total for k, v in sorted(counts.items())} if total else {}


def freshness_age_days(created_at: str, today: date | None = None) -> int:
    """鮮度年齢（日）。"""
    ref = today or datetime.now(tz=UTC).date()
    return (ref - date.fromisoformat(created_at)).days


def _tfidf(texts_a: list[str], texts_b: list[str]) -> tuple[np.ndarray, np.ndarray]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
    matrix = vectorizer.fit_transform(texts_a + texts_b)
    dense = np.asarray(matrix.todense())
    return dense[: len(texts_a)], dense[len(texts_a) :]


def _rbf(x: np.ndarray, y: np.ndarray, gamma: float) -> np.ndarray:
    sq = np.sum(x**2, axis=1)[:, None] + np.sum(y**2, axis=1)[None, :] - 2 * x @ y.T
    out: np.ndarray = np.exp(-gamma * np.maximum(sq, 0))
    return out


def mmd(
    texts_a: list[str], texts_b: list[str], n_permutations: int = 500, seed: int = 20260913
) -> MMDResult:
    """TF-IDF（文字 2〜3-gram）＋ RBF カーネルの MMD。p 値は順列検定で出す（simulated）。"""
    a, b = _tfidf(texts_a, texts_b)
    gamma = 1.0 / max(1, a.shape[1])

    def statistic(x: np.ndarray, y: np.ndarray) -> float:
        kxx = _rbf(x, x, gamma)
        kyy = _rbf(y, y, gamma)
        kxy = _rbf(x, y, gamma)
        n, m = len(x), len(y)
        term_x = (kxx.sum() - np.trace(kxx)) / max(1, n * (n - 1))
        term_y = (kyy.sum() - np.trace(kyy)) / max(1, m * (m - 1))
        return float(term_x + term_y - 2 * kxy.mean())

    observed = statistic(a, b)
    pooled = np.vstack([a, b])
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(n_permutations):
        order = rng.permutation(len(pooled))
        shuffled = pooled[order]
        if statistic(shuffled[: len(a)], shuffled[len(a) :]) >= observed:
            count += 1
    return MMDResult(
        mmd=round(observed, 6),
        p_value=round((count + 1) / (n_permutations + 1), 4),
        n_permutations=n_permutations,
    )


def js_bootstrap_threshold(
    distribution: dict[str, float], n: int, n_boot: int = 500, seed: int = 20260913, q: float = 95.0
) -> float:
    """同一分布からの再サンプルで JS の帰無分布を作り、95% 点を返す。"""
    rng = np.random.default_rng(seed)
    keys = sorted(distribution)
    probs = np.array([distribution[k] for k in keys], dtype=float)
    probs = probs / probs.sum()
    values = []
    for _ in range(n_boot):
        draw_a = rng.multinomial(n, probs) / n
        draw_b = rng.multinomial(n, probs) / n
        values.append(
            js_divergence(
                dict(zip(keys, draw_a, strict=True)), dict(zip(keys, draw_b, strict=True))
            )
        )
    return float(np.percentile(values, q))
