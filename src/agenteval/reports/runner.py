"""実験スクリプトの起動。`experiments/eX_Y_*.py` の `main(live, seed)` を呼ぶ。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from agenteval.core.store import dump_json
from agenteval.llm.cost import REPO_ROOT

EXPERIMENT_DIR = REPO_ROOT / "experiments"
RESULT_DIR = REPO_ROOT / "data" / "results"


def normalize_id(experiment_id: str) -> str:
    """`E3-1` / `e3_1` の揺れを `e3_1` に寄せる。"""
    return experiment_id.lower().replace("-", "_")


def find_script(experiment_id: str) -> Path:
    """実験 ID からスクリプトを探す。"""
    stem = normalize_id(experiment_id)
    for path in sorted(EXPERIMENT_DIR.glob(f"{stem}_*.py")):
        return path
    candidate = EXPERIMENT_DIR / f"{stem}.py"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"実験スクリプトが見つからない: {experiment_id}")


def run_experiment(experiment_id: str, live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    """実験を 1 つ実行し、結果 JSON を保存して返す。"""
    path = find_script(experiment_id)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"読み込めない実験スクリプト: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    result: dict[str, Any] = module.main(live=live, seed=seed)
    dump_json(result, RESULT_DIR / f"{result['id']}.json")
    return result
