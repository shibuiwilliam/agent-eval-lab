"""office 環境。run ごとに SQLite ファイル 1 つ。

現実味より制御性・決定性・スナップショットの安さを優先する（ADR-003）。
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agenteval.core.normalize import sha256_of
from agenteval.env.external import ExternalService

TABLES = ("contacts", "events", "mails", "files", "backups", "notes")
PRIMARY_KEYS = {
    "contacts": "id",
    "events": "id",
    "mails": "id",
    "files": "path",
    "backups": "id",
    "notes": "id",
}

SCHEMA_SQL = """
CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT, email TEXT, dept TEXT);
CREATE TABLE events (id TEXT PRIMARY KEY, title TEXT, start TEXT, end TEXT, room TEXT,
                     attendees TEXT, note TEXT);
CREATE TABLE mails (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, subject TEXT,
                    body TEXT, ts TEXT, folder TEXT);
CREATE TABLE files (path TEXT PRIMARY KEY, content TEXT, updated_at TEXT);
CREATE TABLE backups (id TEXT PRIMARY KEY, source_path TEXT, content TEXT, ts TEXT);
CREATE TABLE notes (id TEXT PRIMARY KEY, text TEXT, ts TEXT);
"""


@dataclass
class SnapshotRef:
    """スナップショット 1 個への参照。"""

    run_id: str
    step: int
    path: Path

    def as_str(self) -> str:
        return str(self.path)


@dataclass
class OfficeEnv:
    """SQLite 1 ファイルのオフィス環境。

    `today` は固定（実時刻に依存させない）。「来週」等の相対日付はここを基準に解く。
    """

    db_path: Path
    today: str = "2026-09-14"  # 月曜
    snapshot_dir: Path | None = None
    run_id: str = "-"
    external: ExternalService = field(default_factory=ExternalService)
    _conn: sqlite3.Connection | None = None
    writes: list[dict[str, Any]] = field(default_factory=list)

    # --- 生成・接続 --------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @classmethod
    def create(cls, db_path: Path, fixture_sql: str, **kwargs: Any) -> OfficeEnv:
        """スキーマ ＋ フィクスチャ SQL から新しい環境を作る。"""
        if db_path.exists():
            db_path.unlink()
        env = cls(db_path=db_path, **kwargs)
        conn = env.connect()
        conn.executescript(SCHEMA_SQL)
        conn.executescript(fixture_sql)
        conn.commit()
        return env

    # --- 状態 --------------------------------------------------------------
    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """全テーブルを主キー順に並べた素の辞書。"""
        conn = self.connect()
        out: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            pk = PRIMARY_KEYS[table]
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}").fetchall()
            out[table] = [dict(r) for r in rows]
        return out

    def state_hash(self) -> str:
        """状態の正規 JSON の sha256。"""
        return sha256_of(self.dump())

    def diff(self, before: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """before（dump の結果）と現在の行単位差分。"""
        after = self.dump()
        result: dict[str, Any] = {"added": [], "changed": [], "removed": []}
        for table in TABLES:
            pk = PRIMARY_KEYS[table]
            b = {r[pk]: r for r in before.get(table, [])}
            a = {r[pk]: r for r in after.get(table, [])}
            for key in sorted(a.keys() - b.keys()):
                result["added"].append({"table": table, "key": key, "row": a[key]})
            for key in sorted(b.keys() - a.keys()):
                result["removed"].append({"table": table, "key": key, "row": b[key]})
            for key in sorted(a.keys() & b.keys()):
                if a[key] != b[key]:
                    result["changed"].append(
                        {"table": table, "key": key, "before": b[key], "after": a[key]}
                    )
        return result

    # --- スナップショット --------------------------------------------------
    def snapshot(self, step: int) -> SnapshotRef:
        """`sqlite3.Connection.backup()` でファイル複製する。"""
        base = self.snapshot_dir or (self.db_path.parent / "snapshots")
        target_dir = base / self.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"step_{step:03d}.sqlite"
        dest = sqlite3.connect(path)
        with dest:
            self.connect().backup(dest)
        dest.close()
        return SnapshotRef(run_id=self.run_id, step=step, path=path)

    def restore(self, ref: SnapshotRef) -> None:
        """スナップショットから状態を戻す。"""
        self.close()
        shutil.copyfile(ref.path, self.db_path)
        self.connect()

    def touched_resources(self) -> set[str]:
        """書込み系ツールが触れたパス・ID の集合。"""
        return {f"{w['kind']}:{w['key']}" for w in self.writes}

    # --- 日付ユーティリティ（決定的） --------------------------------------
    def base_date(self) -> datetime:
        return datetime.fromisoformat(self.today).replace(tzinfo=UTC)

    def resolve_relative_date(self, phrase: str) -> str:
        """「来週」「明日」等を ISO 日付に解く。解けなければ today。"""
        base = self.base_date()
        table = {"今日": 0, "明日": 1, "明後日": 2, "来週": 7, "再来週": 14, "来月": 30}
        for key, delta in table.items():
            if key in phrase:
                return (base + timedelta(days=delta)).strftime("%Y-%m-%d")
        return base.strftime("%Y-%m-%d")

    # --- 低レベル操作（tools.py から使う） ---------------------------------
    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.connect().execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        conn = self.connect()
        conn.execute(sql, params)
        conn.commit()

    def next_id(self, table: str, prefix: str) -> str:
        """決定的な連番 ID（実時刻や乱数を使わない）。"""
        n = self.connect().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return f"{prefix}{n + 1:03d}"

    def record_write(self, kind: str, key: str, tool: str) -> None:
        self.writes.append({"kind": kind, "key": key, "tool": tool})


def dump_snapshot(path: Path) -> dict[str, list[dict[str, Any]]]:
    """スナップショットファイルの内容を dump 形式で読む（分岐再実行の状態復元に使う）。"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        pk = PRIMARY_KEYS[table]
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}").fetchall()
        out[table] = [dict(r) for r in rows]
    conn.close()
    return out
