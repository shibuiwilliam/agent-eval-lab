"""datewright のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。

各ケースは「公開関数の 1 回の呼び出し」の式。期待値は正しいコードで計算して凍結する。
日付や年はケースごとに値を散らす（可視の失敗ケースと識別的リテラルを共有しない隠しケースを残すため）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

PACKAGE = "datewright"
DESCRIPTION = "日付の計算・書式・営業日・期間"
HEADER = [
    "import pytest",
    "from datetime import date, datetime",
    "",
    "",
    "def _iso(span):",
    "    return None if span is None else (span.start.isoformat(), span.end.isoformat())",
    "",
]

TOPICS = {
    "is_leap": "閏年の判定",
    "days_in_month": "月の日数",
    "add_months": "月の加減算",
    "last_day_of_month": "月末日の取得",
    "month_delta": "暦月の差",
    "is_business_day": "営業日の判定",
    "add_business_days": "営業日の加減算",
    "business_days_between": "営業日数の計算",
    "format_date": "日付の書式化",
    "parse_date": "日付の解析",
    "iso_week": "ISO 週の計算",
    "week_start": "週の初日の計算",
    "weeks_in_year": "ISO 年の週数",
    "describe": "相対的な日付の表現",
    "age": "年齢の計算",
    "DateSpan.days": "期間の日数",
    "DateSpan.contains": "期間への包含判定",
    "DateSpan.overlaps": "期間の重なり判定",
    "DateSpan.intersection": "期間の共通部分",
    "DateSpan.months": "期間が触れる月の数",
    "DateSpan.weeks": "期間の週数",
    "DateSpan.midpoint": "期間の中央の日",
    "DateSpan.business_days": "期間内の営業日数",
    "DateSpan.split_by_month": "期間の月ごとの分割",
}

# 紛らわしい名前・似た処理の組（変異の「囮あり」の判定に使う。キーは module.qualname）
DECOYS = [
    ["calendar_math._shift_month", "calendar_math.month_delta"],
    ["calendar_math.days_in_month", "calendar_math.last_day_of_month"],
    ["business.is_business_day", "business._is_weekend"],
    ["relative._days_between", "business.business_days_between"],
]

README_REFS = {
    "is_leap": "4 で割り切れ、100 で割り切れる年は 400 でも割り切れるときだけ閏年",
    "days_in_month": "月が 1〜12 の外なら `ValueError`",
    "add_months": "月末を超える日は、その月の末日に切り詰める",
    "last_day_of_month": "`last_day_of_month(d)`: `d` と同じ月の末日",
    "month_delta": "`b` が後なら正、前なら負",
    "is_business_day": "営業日は土曜・日曜と、`holidays`（祝日の `date` の集まり）に入っていない日",
    "add_business_days": "`n` が 0 なら `d` をそのまま返す（`d` が休日でも動かさない）",
    "business_days_between": "開始日は含まず、終了日は含む",
    "format_date": "`date` には時刻がないので `HH`・`mm` は `00`",
    "parse_date": "`YY` は 00〜69 を 2000 年代、70〜99 を 1900 年代とみなす",
    "iso_week": "年末の数日は翌年の第 1 週、年始の数日は前年の第 52・53 週になりうる",
    "week_start": "`first_weekday` は週の初日の曜日（0 が月曜、6 が日曜）",
    "weeks_in_year": "1 月 1 日が木曜なら 53 週、閏年で水曜でも 53 週",
    "describe": "`near_days` 日以内の未来は「N日後」、過去は「N日前」",
    "age": "2/29 生まれは、閏年でない年には 2/28 に年をとる",
    "DateSpan.days": "両端を含む日数。同じ日の期間は 1 日",
    "DateSpan.contains": "`d` が期間に含まれるか（両端を含む）",
    "DateSpan.overlaps": "1 日でも重なるか。端が同じ日なら重なる",
    "DateSpan.intersection": "重なる部分の期間。重ならなければ `None`",
    "DateSpan.months": "期間が触れる暦月の数（日は見ない）",
    "DateSpan.weeks": "日数を 7 日で割って切り上げた週数",
    "DateSpan.midpoint": "`round`（偶数丸め）が選ぶ方",
    "DateSpan.business_days": "期間内の営業日の数（両端を含む）",
    "DateSpan.split_by_month": "暦月の境目で分けた期間の並び",
}

EDGE = {
    "kind": "ambiguous",
    "tag": "edge_task",
    "readme_ref": "月末を超える日は、その月の末日に切り詰める",
    "prompt": (
        "月次の締め日の計算で、1/31 の 1 か月後を 2/28 ではなく 3/3 にしてほしいという依頼が来ています。"
        "`add_months(date(2023, 1, 31), 1)` が 2023-02-28 を返すのを、2023-03-03 を返すように変えてください。"
    ),
}


def _c(
    target: str,
    module: str,
    call: str,
    *,
    view: str | None = None,
    setup: tuple[str, ...] = (),
    raises: str | None = None,
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "target": target,
        "module": module,
        "call": call,
        "view": view,
        "setup": list(setup),
        "raises": raises,
        "tags": list(tags),
    }


def _d(y: int, m: int, d: int) -> str:
    return f"date({y}, {m}, {d})"


def _t(x: dt.date) -> tuple[int, int, int]:
    return (x.year, x.month, x.day)


def _span(a: tuple[int, int, int], b: tuple[int, int, int], name: str = "sp") -> str:
    return f"{name} = DateSpan({_d(*a)}, {_d(*b)})"


ISO = "$.isoformat()"
_RESERVED = {1900, 2000, 2024, 2100}


class _Years:
    """ケースごとに別の年を配る（可視の失敗ケースと識別的リテラルを共有する隠しケースを残すため）。"""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.pool: list[int] = []
        self._refill()

    def _refill(self) -> None:
        years = self.rng.permutation(np.arange(1951, 2090))
        self.pool += [int(y) for y in years if int(y) not in _RESERVED]

    def any(self) -> int:
        if not self.pool:
            self._refill()
        return self.pool.pop(0)

    def pick(self, leap: bool) -> int:
        while True:
            for i, y in enumerate(self.pool):
                if (y % 4 == 0) == leap:
                    return self.pool.pop(i)
            self._refill()


def _find(y: int, m: int, weekday: int, nth: int = 0) -> dt.date:
    """y 年 m 月の weekday 曜日（0 が月曜）のうち nth 番目の日。"""
    first = dt.date(y, m, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * nth)


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    cs: list[dict[str, Any]] = []
    Y = _Years(rng)

    def md(lo: int = 3, hi: int = 12) -> int:
        return int(rng.integers(lo, hi + 1))

    def day(lo: int = 3, hi: int = 28) -> int:
        return int(rng.integers(lo, hi + 1))

    # --- is_leap ---
    for y, tag in [(1900, "boundary"), (2000, "boundary"), (2100, "boundary"), (2024, ""), (Y.pick(True), ""), (Y.pick(False), "")]:
        cs.append(_c("is_leap", "calendar_math", f"is_leap({y})", tags=(tag,) if tag else ()))
    # --- days_in_month ---
    for y, m in [(Y.pick(True), 2), (Y.pick(False), 2), (1900, 2), (2000, 2), (Y.any(), 4), (Y.any(), 12), (Y.any(), 11), (Y.any(), 9)]:
        cs.append(_c("days_in_month", "calendar_math", f"days_in_month({y}, {m})"))
    cs.append(_c("days_in_month", "calendar_math", f"days_in_month({Y.any()}, 13)", raises="ValueError", tags=("boundary",)))
    cs.append(_c("days_in_month", "calendar_math", f"days_in_month({Y.any()}, 0)", raises="ValueError", tags=("boundary",)))
    # --- add_months ---
    am: list[tuple[int, int, int, int, str]] = [
        (2023, 1, 31, 1, "edge_task"),
        (Y.pick(True), 1, 31, 1, ""),
        (Y.any(), 3, 31, -1, ""),
        (Y.any(), 12, day(), 2, ""),
        (Y.any(), 1, day(), -13, ""),
        (Y.any(), 5, 31, 9, ""),
        (Y.any(), 10, 31, 4, ""),
        (Y.pick(True), 2, 29, 12, ""),
        (Y.pick(True), 2, 29, 48, ""),
        (Y.any(), md(), day(), 0, "boundary"),
        (Y.any(), 12, 31, 2, ""),
        (Y.any(), 6, 30, -6, ""),
        (Y.any(), 7, 31, -1, ""),
        (Y.any(), 11, 30, 3, ""),
        (Y.any(), 7, 19, -30, ""),
        (Y.pick(True), 2, 29, -12, ""),
    ]
    for y, m, d, n, tag in am:
        cs.append(_c("add_months", "calendar_math", f"add_months({_d(y, m, d)}, {n})", view=ISO, tags=(tag,) if tag else ()))
    # --- last_day_of_month ---
    for y, m, d in [(Y.pick(True), 2, day()), (Y.pick(False), 2, day()), (Y.any(), 4, 30), (Y.any(), 12, 5)]:
        cs.append(_c("last_day_of_month", "calendar_math", f"last_day_of_month({_d(y, m, d)})", view=ISO))
    # --- month_delta ---
    for a, b in [
        ((Y.any(), 3, 31), (Y.any(), 2, 1)),
        ((2024, 5, 20), (2024, 5, 3)),
        ((Y.any(), 8, 17), (Y.any(), 11, 28)),
        ((Y.any(), 12, 31), (Y.any(), 1, 1)),
        ((Y.any(), 6, 15), (Y.any(), 6, 22)),
    ]:
        cs.append(_c("month_delta", "calendar_math", f"month_delta({_d(*a)}, {_d(*b)})"))
    # --- is_business_day ---
    def hol_year() -> tuple[int, int]:
        return Y.any(), md()

    y, m = hol_year()
    sat, sun, mon = _find(y, m, 5), _find(y, m, 6), _find(y, m, 0)
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(sat))})"))
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(sun))})"))
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(mon))})"))
    y, m = hol_year()
    fri = _find(y, m, 4, 1)
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(fri))})"))
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(fri))}, [{_d(*_t(fri))}])"))
    y, m = hol_year()
    mon2 = _find(y, m, 0, 2)
    tue2 = mon2 + dt.timedelta(days=1)
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(mon2))}, frozenset({{{_d(*_t(mon2))}}}))"))
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(tue2))}, frozenset({{{_d(*_t(mon2))}}}))"))
    y, m = hol_year()
    sat3 = _find(y, m, 5, 1)
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(sat3))}, [{_d(*_t(sat3))}])"))
    y, m = hol_year()
    wed = _find(y, m, 2)
    cs.append(_c("is_business_day", "business", f"is_business_day({_d(*_t(wed))}, [])", tags=("empty",)))
    # --- add_business_days ---
    def bd(weekday: int, nth: int = 0) -> dt.date:
        y2, m2 = hol_year()
        return _find(y2, m2, weekday, nth)

    def hl(*ds: dt.date) -> str:
        return "[" + ", ".join(_d(*_t(x)) for x in ds) + "]"

    f1, f2, m3, s4, s5, s6 = bd(4), bd(4, 1), bd(0, 1), bd(5), bd(5, 1), bd(5, 2)
    for start, n in [(f1, 1), (f2, 5), (m3, -1), (s4, 0), (s5, 1), (s6, -1)]:
        cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(start))}, {n})", view=ISO))
    t7 = bd(3)
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(t7))}, 3, {hl(t7 + dt.timedelta(days=1), t7 + dt.timedelta(days=4))})", view=ISO))
    t8 = bd(4, 2)
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(t8))}, 2, {hl(t8 + dt.timedelta(days=3))})", view=ISO))
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(bd(2)))}, 10)", view=ISO))
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(bd(1, 1)))}, -7)", view=ISO))
    t11 = bd(0, 2)
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(t11))}, 25, {hl(t11 + dt.timedelta(days=9), t11 + dt.timedelta(days=16))})", view=ISO))
    t12 = bd(1, 2)
    cs.append(_c("add_business_days", "business", f"add_business_days({_d(*_t(t12))}, -3, {hl(t12 - dt.timedelta(days=1))})", view=ISO))
    # --- business_days_between ---
    for k in range(10):
        a = bd(int(rng.integers(0, 7)), int(rng.integers(0, 3)))
        span_days = [7, 0, -7, 7, 11, 1, 1, 100, 1, -1][k]
        b = a + dt.timedelta(days=span_days)
        extra = ["", "", "", ", None, False", f", {hl(a + dt.timedelta(days=3), a + dt.timedelta(days=8))}", "", ", None, False", f", {hl(a + dt.timedelta(days=30))}", ", [], False", ", [], False"][k]
        cs.append(_c("business_days_between", "business", f"business_days_between({_d(*_t(a))}, {_d(*_t(b))}{extra})"))
    # --- format_date ---
    fixed_fmt = [
        (None, ""),
        (None, ""),
        (None, ""),
        (None, ""),
        (None, ""),
        (None, ""),
        (None, ""),
        ("'YY/M/D'", ""),
        ("'ddd, DD MM YYYY'", ""),
        ("\"'Q'M-D YY\"", ""),
        ("'HH:mm'", ""),
        ("''", "empty"),
    ]
    for pat, tag in fixed_fmt:
        yy = Y.any()
        pat_s = "" if pat is None else f", {pat}"
        cs.append(_c("format_date", "patterns", f"format_date({_d(yy, md(), day())}{pat_s})", tags=(tag,) if tag else ()))
    cs.append(_c("format_date", "patterns", f"format_date(datetime({Y.any()}, {md()}, {day()}, 9, 5), 'YYYYMMDD HH:mm')"))
    cs.append(_c("format_date", "patterns", "format_date(date(987, 5, 6))", tags=("boundary",)))
    # --- parse_date ---
    def iso(y3: int, m3: int, d3: int) -> str:
        return f"{y3:04d}-{m3:02d}-{d3:02d}"

    for _ in range(3):
        cs.append(_c("parse_date", "patterns", f"parse_date('{iso(Y.any(), md(), day())}')", view=ISO))
    cs.append(_c("parse_date", "patterns", f"parse_date('{iso(Y.pick(True), 2, 29)}')", view=ISO, tags=("boundary",)))
    yy = Y.any()
    mm, dd = md(), day()
    cs.append(_c("parse_date", "patterns", f"parse_date('{dd:02d}/{mm:02d}/{yy}', 'DD/MM/YYYY')", view=ISO))
    cs.append(_c("parse_date", "patterns", f"parse_date('{day()}/{md()}/{int(rng.integers(3, 69)):02d}', 'D/M/YY')", view=ISO))
    cs.append(_c("parse_date", "patterns", f"parse_date('{day()}/{md()}/69', 'D/M/YY')", view=ISO, tags=("boundary",)))
    cs.append(_c("parse_date", "patterns", f"parse_date('{day()}/{md()}/70', 'D/M/YY')", view=ISO, tags=("boundary",)))
    wk = _find(Y.any(), md(), 6)
    cs.append(_c("parse_date", "patterns", f"parse_date('Sun {iso(*_t(wk))}', 'ddd YYYY-MM-DD')", view=ISO))
    y2 = Y.pick(False)
    for call in [
        f"parse_date('{iso(y2, 2, 29)}')",
        f"parse_date('{iso(Y.any(), 4, 31)}')",
        f"parse_date('{Y.any()}-13-{day():02d}')",
        f"parse_date('24-{md()}-{day()}')",
    ]:
        cs.append(_c("parse_date", "patterns", call, raises="ValueError"))
    mon_s = _find(Y.any(), md(), 0)
    cs.append(_c("parse_date", "patterns", f"parse_date('Fri {iso(*_t(mon_s))}', 'ddd YYYY-MM-DD')", raises="ValueError"))
    cs.append(_c("parse_date", "patterns", f"parse_date('{iso(Y.any(), md(), day())} 10:30', 'YYYY-MM-DD HH:mm')", raises="ValueError"))
    # --- iso_week ---
    for dd3 in [(2021, 1, 3), (2020, 12, 31), (2024, 12, 30), (2016, 1, 1), (2018, 12, 31), (Y.any(), md(), day()), (2012, 1, 1), (Y.any(), md(), day())]:
        cs.append(_c("iso_week", "isoweek", f"iso_week({_d(*dd3)})"))
    # --- week_start ---
    for fw in [None, None, None, None, None, None, None, 6, 3]:
        pat_s = "" if fw is None else f", {fw}"
        cs.append(_c("week_start", "isoweek", f"week_start({_d(Y.any(), md(), day())}{pat_s})", view=ISO))
    # --- weeks_in_year ---
    for y in [2020, 2015, Y.any(), Y.any(), 2004]:
        cs.append(_c("weeks_in_year", "isoweek", f"weeks_in_year({y})"))
    # --- describe ---
    def desc(delta: int, extra: str = "") -> None:
        t0 = dt.date(Y.any(), md(), day())
        target = t0 + dt.timedelta(days=delta)
        cs.append(_c("describe", "relative", f"describe({_d(*_t(target))}, {_d(*_t(t0))}{extra})"))

    for delta in [0, 1, -1, 6, -6, 3, 7, 7, 7, 7, -7, -7, -7, 45]:
        desc(delta)
    desc(9, ", 10")
    desc(-10, ", 10")
    # --- age ---
    ag: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = []
    b1 = dt.date(Y.any(), md(), day())
    ag.append((_t(b1), _t(b1.replace(year=b1.year + 34))))
    b2 = dt.date(Y.any(), md(4), day(4))
    ag.append((_t(b2), _t(b2.replace(year=b2.year + 27) - dt.timedelta(days=1))))
    yl = Y.pick(True)
    yn = yl + 3
    ag.append(((yl, 2, 29), (yn, 2, 28)))
    ag.append(((yl, 2, 29), (yn, 2, 27)))
    ag.append(((yl, 2, 29), (yl + 4, 2, 29)))
    b6 = dt.date(Y.any(), md(), day())
    ag.append((_t(b6), _t(b6)))
    ag.append(((Y.any(), 12, 31), (2031, 1, 1)))
    b8 = dt.date(Y.any(), md(), day())
    ag.append((_t(b8), _t(b8.replace(year=b8.year + 9) + dt.timedelta(days=45))))
    for b, t in ag:
        if b <= t:
            cs.append(_c("age", "relative", f"age({_d(*b)}, {_d(*t)})"))
    cs.append(_c("age", "relative", f"age({_d(2024, 5, 16)}, {_d(2024, 5, 15)})", raises="ValueError"))
    # --- DateSpan ---
    def rspan(length: int, name: str = "sp") -> tuple[str, dt.date, dt.date]:
        start = dt.date(Y.any(), md(), day())
        end = start + dt.timedelta(days=length - 1)
        return _span(_t(start), _t(end), name), start, end

    for length in [1, 38]:
        cs.append(_c("DateSpan.days", "spans", "sp.days()", setup=(rspan(length)[0],)))
    for kind in ["start", "end", "inside", "outside"]:
        setup, a, b = rspan(24)
        probe = {"start": a, "end": b, "inside": a + dt.timedelta(days=11), "outside": a - dt.timedelta(days=1)}[kind]
        cs.append(_c("DateSpan.contains", "spans", f"sp.contains({_d(*_t(probe))})", setup=(setup,)))
    for off1, len1, off2, len2 in [(23, 20, 0, 24), (0, 24, 24, 5), (0, 24, 3, 9), (3, 9, 0, 24)]:
        s1, a, b = rspan(24)
        o_start = a + dt.timedelta(days=off1 if off1 == 23 else off2 if off2 == 24 else 0)
        o = _span(_t(o_start), _t(o_start + dt.timedelta(days=len2 - 1)), "other")
        cs.append(_c("DateSpan.overlaps", "spans", "sp.overlaps(other)", setup=(s1, o)))
    for shift, length2 in [(23, 20), (24, 5), (-5, 9), (7, 7), (0, 24), (-30, 80), (10, 4)]:
        s1, a, b = rspan(24)
        o_start = a + dt.timedelta(days=shift)
        o = _span(_t(o_start), _t(o_start + dt.timedelta(days=length2 - 1)), "other")
        cs.append(_c("DateSpan.intersection", "spans", "sp.intersection(other)", view="_iso($)", setup=(s1, o)))
    for length in [13, 38, 140]:
        cs.append(_c("DateSpan.months", "spans", "sp.months()", setup=(rspan(length)[0],)))
    for length in [7, 8, 13, 15, 20, 1, 49, 100, 6]:
        cs.append(_c("DateSpan.weeks", "spans", "sp.weeks()", setup=(rspan(length)[0],)))
    for length in [4, 8, 12, 16, 20, 24, 28, 1, 5, 6]:
        cs.append(_c("DateSpan.midpoint", "spans", "sp.midpoint()", view=ISO, setup=(rspan(length)[0],)))
    for length, nh in [(12, 0), (12, 1), (2, 0), (1, 0), (19, 2), (33, 0)]:
        setup, a, b = rspan(length)
        if nh:
            hh = [a + dt.timedelta(days=3 + 7 * i) for i in range(nh)]
            cs.append(_c("DateSpan.business_days", "spans", f"sp.business_days({hl(*hh)})", setup=(setup,)))
        else:
            cs.append(_c("DateSpan.business_days", "spans", "sp.business_days()", setup=(setup,)))
    for length in [64, 13, 85, 32, 28, 120]:
        s, a, b = rspan(length)
        cs.append(_c("DateSpan.split_by_month", "spans", "sp.split_by_month()", view="[_iso(s) for s in $]", setup=(s,)))
    return cs
