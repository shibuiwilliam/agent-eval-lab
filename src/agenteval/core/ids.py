"""識別子とハッシュ。run_id は決定的に作る（再実行で同じ id になる）。"""

from __future__ import annotations

from agenteval.core.normalize import sha256_of


def run_id(task_id: str, version_id: str, seed: int, repeat: int, mode: str) -> str:
    """1 run の識別子。(task, version, seed, repeat, mode) から決定的に導く。"""
    digest = sha256_of([task_id, version_id, seed, repeat, mode])[:12]
    return f"{task_id}__{version_id}__r{repeat}__{digest}"


def branch_run_id(parent: str, new_version: str) -> str:
    """分岐再実行の run_id。"""
    return f"br__{new_version}__{sha256_of([parent, new_version])[:12]}"
