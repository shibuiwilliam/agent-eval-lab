"""trace の移行検査（ADR-031、docs/plan/coding/SPEC.md 3.3 節）。

`data/traces/` の全 trace を新スキーマで読み、次の 3 つを数える（読むだけで何も書かない）。
- 読めなかったファイル
- 往復（読み込み → JSON 化 → 読み込み）で既存項目や trace_hash が変わったファイル
- 新項目（effects / policy_decisions / env_kind）が既定値でなかったファイル

pytest からは呼ばない（data/ に依存させない）。実行: `uv run python scripts/check_schema_migration.py [DIR]`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agenteval.core.schema import Run
from agenteval.core.store import TRACE_DIR


class MigrationReport(BaseModel):
    """検査結果。3 つのリストが全部空なら移行は無害。"""

    directory: str
    n_files: int
    load_failures: list[str]  # 読めなかったファイル名
    roundtrip_changed: list[str]  # 往復で既存項目が変わったファイル名
    nondefault_new_fields: list[
        str
    ]  # 新項目が既定値でなかったファイル名（旧 trace なら 0 件のはず）

    @property
    def n_problems(self) -> int:
        return (
            len(self.load_failures) + len(self.roundtrip_changed) + len(self.nondefault_new_fields)
        )


def is_subset(raw: Any, dumped: Any) -> bool:
    """raw の持つ項目がすべて dumped に同じ値で残っているか（dumped の追加項目は許す）。"""
    if isinstance(raw, dict):
        return isinstance(dumped, dict) and all(
            k in dumped and is_subset(v, dumped[k]) for k, v in raw.items()
        )
    if isinstance(raw, list):
        return (
            isinstance(dumped, list)
            and len(raw) == len(dumped)
            and all(is_subset(a, b) for a, b in zip(raw, dumped, strict=True))
        )
    return bool(raw == dumped)


def _has_nondefault_new_fields(run: Run) -> bool:
    return run.env_kind != "office" or any(s.effects or s.policy_decisions for s in run.steps)


def check_file(path: Path) -> tuple[bool, bool, bool]:
    """(読めなかった, 往復で変わった, 新項目が既定値でない) を返す。"""
    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
        run = Run.model_validate_json(text)
    except Exception:  # noqa: BLE001 どんな失敗も「読めなかった」として数える
        return True, False, False
    # 旧スキーマとの一致は「元の JSON の項目が書き出し後に同じ値で残っているか」で見る。
    # 新スキーマのモデル同士の往復比較は健全性チェックにすぎず何も検出しないので置かない。
    # trace_hash の不変は tests/core/test_schema_v2.py のハッシュ不変テストが担保する
    changed = not is_subset(raw, json.loads(run.model_dump_json()))
    return False, changed, _has_nondefault_new_fields(run)


def check_dir(directory: Path) -> MigrationReport:
    """ディレクトリ直下の *.json を全部検査する。`*.partial.json` は対象外（ADR-040）。"""
    files = (
        sorted(p for p in directory.glob("*.json") if not p.name.endswith(".partial.json"))
        if directory.exists()
        else []
    )
    load_failures: list[str] = []
    roundtrip_changed: list[str] = []
    nondefault: list[str] = []
    for path in files:
        failed, changed, new_fields = check_file(path)
        if failed:
            load_failures.append(path.name)
        if changed:
            roundtrip_changed.append(path.name)
        if new_fields:
            nondefault.append(path.name)
    return MigrationReport(
        directory=str(directory),
        n_files=len(files),
        load_failures=load_failures,
        roundtrip_changed=roundtrip_changed,
        nondefault_new_fields=nondefault,
    )


def main(argv: list[str] | None = None) -> int:
    """引数: ディレクトリ（既定 data/traces）。問題が 1 件でもあれば 1 を返す。"""
    args = sys.argv[1:] if argv is None else argv
    directory = Path(args[0]) if args else TRACE_DIR
    report = check_dir(directory)
    if report.n_files == 0:
        # 階層付きの data/coding/traces/ の親を渡すなどで「0 本で成功」になる事故を防ぐ
        print(f"WARNING: 検査対象が 0 本: {report.directory}", file=sys.stderr)
        return 1
    print(f"directory: {report.directory}")
    print(f"n_files: {report.n_files}")
    print(f"load_failures: {len(report.load_failures)}")
    print(f"roundtrip_changed: {len(report.roundtrip_changed)}")
    print(f"nondefault_new_fields: {len(report.nondefault_new_fields)}")
    for label, names in (
        ("load_failures", report.load_failures),
        ("roundtrip_changed", report.roundtrip_changed),
        ("nondefault_new_fields", report.nondefault_new_fields),
    ):
        for name in names[:20]:
            print(f"  {label}: {name}")
    return 1 if report.n_problems > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
