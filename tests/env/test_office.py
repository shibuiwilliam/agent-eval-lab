"""環境のスナップショット・ハッシュ・差分の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.env.external import ExternalService
from agenteval.env.fixtures import load_sql
from agenteval.env.office import OfficeEnv


@pytest.fixture
def env(tmp_path: Path) -> OfficeEnv:
    return OfficeEnv.create(tmp_path / "e.sqlite", load_sql("office_small_v1"), run_id="t")


def test_state_hash_is_stable(env: OfficeEnv) -> None:
    assert env.state_hash() == env.state_hash()


def test_snapshot_restore_roundtrip(env: OfficeEnv) -> None:
    before = env.state_hash()
    ref = env.snapshot(0)
    env.execute("INSERT INTO notes VALUES ('n9','x','2026-09-14T00:00')")
    assert env.state_hash() != before
    env.restore(ref)
    assert env.state_hash() == before


def test_diff_detects_row_changes(env: OfficeEnv) -> None:
    before = env.dump()
    env.execute("UPDATE files SET content = 'changed' WHERE path = 'docs/spec.md'")
    diff = env.diff(before)
    assert diff["changed"] and diff["changed"][0]["key"] == "docs/spec.md"
    assert not diff["added"] and not diff["removed"]


def test_external_version_changes_keys() -> None:
    v1 = ExternalService("v1").lookup("projector")
    v2 = ExternalService("v2").lookup("projector")
    assert "price" in v1 and "price" not in v2
    assert v2["unit_price"] != v1["price"]
