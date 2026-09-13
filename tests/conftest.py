"""pytest の共通設定。

CLAUDE.md の「絶対に守ること」:
  - pytest から live API を呼ばない
  - `AGENTEVAL_LLM_MODE=replay` を強制する
遮断は二重にする:
  1. 環境変数を replay に固定する（`LLMClient` の既定モードが live にならない）
  2. `socket.socket` を差し替えて外部接続そのものを失敗させる
run を作るテストは `LLMClient(mode="sim", simulator=...)` を明示的に組み立てる
（sim は API を呼ばない。ADR-008）。
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ["AGENTEVAL_LLM_MODE"] = "replay"
os.environ.pop("ANTHROPIC_API_KEY", None)


class _BlockedSocket(socket.socket):
    """外部接続を禁止するソケット。"""

    def connect(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - 失敗させるだけ
        raise RuntimeError("tests から外部ネットワークに接続しようとした")

    def connect_ex(self, *args: Any, **kwargs: Any) -> int:  # pragma: no cover
        raise RuntimeError("tests から外部ネットワークに接続しようとした")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket, "socket", _BlockedSocket)
    yield


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """コスト台帳をテスト用の一時ファイルに逃がす。"""
    from agenteval.llm import cost

    monkeypatch.setattr(cost, "LEDGER_PATH", tmp_path / "cost_ledger.jsonl")
    yield


@pytest.fixture
def sim_client() -> Any:
    """sim モードのクライアントを作るファクトリ。"""
    from agenteval.llm.client import LLMClient
    from agenteval.llm.simulator import Simulator

    def factory(task: Any, seed: int = 1, repeat: int = 0) -> LLMClient:
        return LLMClient(
            mode="sim", run_id="test", simulator=Simulator(task=task, seed=seed, repeat=repeat)
        )

    return factory
