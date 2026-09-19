"""結果ページの英語版に差し込む文章の読み込み。

数値・図・判定は `data/results/E*.json` と `registry.yaml` から来るので言語に依存しない。
言語に依存するのは「タイトル・5項目・方法・判定理由・気づき」の文章だけで、
その英訳を `experiments/registry.en.yaml` に 1 か所へ集める。

実験スクリプトを触らずに英語ページを再生成できるようにするための分離であり、
逆に言うと日本語側を直したら英訳も直す必要がある（`agenteval pages` が欠落を警告する）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agenteval.llm.cost import REPO_ROOT

EN_REGISTRY_PATH = REPO_ROOT / "experiments" / "registry.en.yaml"


def load_en(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """実験 ID → 英語の文章。ファイルが無ければ空を返す。"""
    target = path or EN_REGISTRY_PATH
    if not target.exists():
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    entries: list[dict[str, Any]] = data if isinstance(data, list) else data.get("experiments", [])
    return {entry["id"]: entry for entry in entries if "id" in entry}


def missing_ids(registry_ids: list[str], path: Path | None = None) -> list[str]:
    """英訳が無い実験 ID。"""
    have = load_en(path)
    return [experiment_id for experiment_id in registry_ids if experiment_id not in have]
