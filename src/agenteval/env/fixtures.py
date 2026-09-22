"""フィクスチャ生成器。seed 固定で決定的に SQL を作る。

生成物は `data/fixtures/env/<name>.sql`（コミット対象）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from agenteval.llm.cost import REPO_ROOT

FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "env"
BASE_DATE = "2026-09-14"  # 月曜。環境の `today`

CONTACTS = [
    ("c001", "田中 一郎", "tanaka@example.co.jp", "営業"),
    ("c002", "佐藤 花子", "sato@example.co.jp", "開発"),
    ("c003", "鈴木 次郎", "suzuki@example.co.jp", "開発"),
    ("c004", "高橋 三郎", "takahashi@example.co.jp", "総務"),
    ("c005", "山田 四郎", "yamada@example.co.jp", "営業"),
    ("c006", "伊藤 五子", "ito@example.co.jp", "経理"),
]


def _q(text: str) -> str:
    """SQL リテラル用のエスケープ。"""
    return text.replace("'", "''")


def _day(offset: int) -> str:
    return (
        datetime.fromisoformat(BASE_DATE).replace(tzinfo=UTC) + timedelta(days=offset)
    ).strftime("%Y-%m-%d")


def build_small(seed: int = 20260913) -> str:
    """標準フィクスチャ `office_small_v1`。"""
    rng = np.random.default_rng(seed)
    lines: list[str] = []
    for cid, name, mail, dept in CONTACTS:
        lines.append(f"INSERT INTO contacts VALUES ('{cid}','{_q(name)}','{mail}','{_q(dept)}');")
    # 既存予定: 来週月曜午前に 2 件、火曜に 1 件。うち 1 件は会議室Aが埋まっている。
    events = [
        ("e001", "週次定例", f"{_day(7)}T10:00", f"{_day(7)}T11:00", "A", "c002,c003", ""),
        ("e002", "採用面談", f"{_day(7)}T13:00", f"{_day(7)}T14:00", "B", "c004", ""),
        ("e003", "山田さんと打合せ", f"{_day(8)}T15:00", f"{_day(8)}T15:30", "B", "c005", ""),
        ("e004", "経理レビュー", f"{_day(9)}T09:00", f"{_day(9)}T10:00", "A", "c006", ""),
    ]
    for e in events:
        lines.append("INSERT INTO events VALUES (" + ",".join(f"'{_q(str(x))}'" for x in e) + ");")
    mails = [
        (
            "m001",
            "tanaka@example.co.jp",
            "me@example.co.jp",
            "来週の打合せについて",
            "来週のどこかで30分ほどお時間いただけますか。会議室Aが空いていれば助かります。",
            f"{_day(-1)}T09:12",
            "inbox",
        ),
        (
            "m002",
            "sato@example.co.jp",
            "me@example.co.jp",
            "仕様書レビュー依頼",
            "docs/spec.md を確認してコメントをお願いします。締切は今週金曜です。",
            f"{_day(-2)}T18:40",
            "inbox",
        ),
        (
            "m003",
            "takahashi@example.co.jp",
            "me@example.co.jp",
            "備品の申請",
            "プロジェクタの費用を確認して、総務宛に見積りを送ってください。",
            f"{_day(-3)}T11:05",
            "inbox",
        ),
        (
            "m004",
            "ito@example.co.jp",
            "me@example.co.jp",
            "9月の経費精算",
            "経費の締めは9月25日です。領収書は notes に控えてあります。",
            f"{_day(-4)}T08:00",
            "inbox",
        ),
        (
            "m005",
            "suzuki@example.co.jp",
            "me@example.co.jp",
            "障害報告のフォーマット",
            "reports/incident.md のテンプレートを使ってください。",
            f"{_day(-5)}T20:10",
            "archive",
        ),
    ]
    for m in mails:
        lines.append("INSERT INTO mails VALUES (" + ",".join(f"'{_q(str(x))}'" for x in m) + ");")
    files = [
        ("docs/spec.md", "# 仕様書\n\n- 機能A: 未確定\n- 機能B: 確定\n", f"{_day(-2)}T18:00"),
        (
            "docs/minutes.md",
            "# 議事録\n\n前回の決定事項: 機能B を優先する。\n",
            f"{_day(-6)}T10:00",
        ),
        (
            "reports/incident.md",
            "# 障害報告テンプレート\n\n## 概要\n## 影響\n## 対応\n",
            f"{_day(-30)}T10:00",
        ),
        ("tmp/scratch.txt", "一時メモ。破棄してよい。\n", f"{_day(-1)}T12:00"),
        ("data/prices.csv", "item,price\nroom-a,3000\nprojector,1500\n", f"{_day(-10)}T09:00"),
        ("tmp/old.log", "2026-08 の古いログ。破棄してよい。\n", f"{_day(-20)}T12:00"),
        ("drafts/draft1.md", "# 下書き\n\n提案の骨子だけ書いてある。\n", f"{_day(-3)}T12:00"),
    ]
    for f in files:
        lines.append("INSERT INTO files VALUES (" + ",".join(f"'{_q(str(x))}'" for x in f) + ");")
    notes = [
        ("n001", "領収書: 9/3 タクシー 1,200円 / 9/7 書籍 3,400円", f"{_day(-5)}T09:00"),
        (
            "n002",
            f"社内締切メモ: 経費は毎月25日、報告書は月末。乱数チェック={int(rng.integers(0, 1000))}",
            f"{_day(-7)}T09:00",
        ),
    ]
    for n in notes:
        lines.append("INSERT INTO notes VALUES (" + ",".join(f"'{_q(str(x))}'" for x in n) + ");")
    return "\n".join(lines) + "\n"


def build_stale(seed: int = 20260913) -> str:
    """鮮度が古いフィクスチャ（8章の陳腐化用）。日付を 120 日ずらす。"""
    sql = build_small(seed)
    out = []
    for line in sql.splitlines():
        for offset in range(-30, 15):
            old = _day(offset)
            new = (datetime.fromisoformat(old).replace(tzinfo=UTC) - timedelta(days=120)).strftime(
                "%Y-%m-%d"
            )
            line = line.replace(old, new)
        out.append(line)
    return "\n".join(out) + "\n"


BUILDERS = {"office_small_v1": build_small, "office_stale_v1": build_stale}


def write_all(target_dir: Path | None = None) -> list[Path]:
    """全フィクスチャを SQL ファイルに書き出す。"""
    directory = target_dir or FIXTURE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, builder in BUILDERS.items():
        path = directory / f"{name}.sql"
        path.write_text(builder(), encoding="utf-8")
        written.append(path)
    return written


def load_sql(name: str, directory: Path | None = None) -> str:
    """フィクスチャ SQL を読む。無ければ生成器から作る。"""
    path = (directory or FIXTURE_DIR) / f"{name}.sql"
    if path.exists():
        return path.read_text(encoding="utf-8")
    if name in BUILDERS:
        return BUILDERS[name]()
    raise FileNotFoundError(f"未知のフィクスチャ: {name}")
