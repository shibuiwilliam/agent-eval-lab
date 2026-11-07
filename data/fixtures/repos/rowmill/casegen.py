"""rowmill のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。"""

from __future__ import annotations

from typing import Any

import numpy as np

PACKAGE = "rowmill"
DESCRIPTION = "CSV の読み書き・型付け・絞り込み・集計"
HEADER = ["import pytest", "from datetime import date"]

TOPICS = {
    "detect_delimiter": "区切り文字の推定",
    "read_rows": "CSV の読み込み",
    "coerce": "セルの型変換",
    "apply_schema": "列定義の適用",
    "normalize_header": "見出しの正規化",
    "select": "列の選択",
    "rename": "列名の変更",
    "filter_rows": "行の絞り込み",
    "dedupe": "重複行の排除",
    "group_by": "グループ集計",
    "write_rows": "CSV の書き出し",
}

# 紛らわしい名前・似た処理の組（キーは module.qualname）
DECOYS = [
    ["schema._cast", "schema.coerce"],
    ["dialect.parse_number", "schema._cast"],
    ["dialect.parse_bool", "schema._cast"],
    ["dialect.parse_iso_date", "schema._cast"],
    ["dialect._count_outside_quotes", "writer._needs_quote"],
    ["reader._dedupe_headers", "schema.normalize_header"],
    ["schema.is_blank", "schema.format_value"],
    ["dialect.strip_bom", "reader._dedupe_headers"],
    ["dialect.parse_percent", "dialect.parse_accounting"],
]

README_REFS = {
    "detect_delimiter": "`sample` の先頭 20 行（空行を除く）のすべてで、引用符の外の出現数が同じで 0 でない候補を返す",
    "read_rows": "`skip_blank` が真なら、空行と全セルが空白の行を捨てる",
    "coerce": "`None` と空白だけの文字列は `None`",
    "apply_schema": "`columns` にある列だけを、`columns` の順に残す",
    "normalize_header": "`max_len` 文字を超える分は切り捨てる。英数字が無ければ `col`",
    "select": "`columns` の列だけを、その順に取り出す。行に無い列は `None`",
    "rename": "`mapping` にある列は対応する名前に",
    "filter_rows": "空のセルと、`value` の型に変換できないセルは、`ne` を含むどの `op` にも一致しない",
    "dedupe": '`keep="first"` は最初に現れた行を、`keep="last"` は最後に現れた行を残す',
    "group_by": "集計は空のセルを飛ばす。",
    "write_rows": "区切り・引用符・改行・前後の空白を含むセルと、`quote_all` が真のときの全セルは引用符で囲み",
}

EDGE = {
    "kind": "do_nothing",
    "tag": "edge_task",
    "readme_ref": "引用符の中の区切り・改行・`\"\"` は 1 つのセルの中身になる",
    "prompt": (
        "CSV を読み込むとき、引用符で囲んだセルの中に改行があると、そこで行が割れて別の行になってしまうという報告が"
        "来ています。たとえば `read_rows('name,note\\n\"widget\",\"line one\\nline two\"\\nbolt,plain\\n')` が"
        "壊れる、とのことです。再現するか確認して、必要なら直してください。"
        "再現しない場合は、変更せずにその旨を報告してください。"
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


def _dd(call: str, **kw: Any) -> dict[str, Any]:
    return _c("detect_delimiter", "dialect", call, **kw)


def _rr(call: str, **kw: Any) -> dict[str, Any]:
    return _c("read_rows", "reader", call, **kw)


def _co(call: str, **kw: Any) -> dict[str, Any]:
    return _c("coerce", "schema", call, **kw)


def _ap(call: str, **kw: Any) -> dict[str, Any]:
    return _c("apply_schema", "schema", call, **kw)


def _nh(call: str, **kw: Any) -> dict[str, Any]:
    return _c("normalize_header", "schema", call, **kw)


def _tf(target: str, call: str, **kw: Any) -> dict[str, Any]:
    return _c(target, "transform", call, **kw)


def _gb(call: str, **kw: Any) -> dict[str, Any]:
    return _c("group_by", "aggregate", call, **kw)


def _wr(call: str, **kw: Any) -> dict[str, Any]:
    return _c("write_rows", "writer", call, **kw)



class Fresh:
    """ケースごとに別のリテラルを払い出す（識別的リテラルを他のケースと共有しないため）。"""

    _SYL = ["ba", "ke", "lo", "mi", "ru", "sa", "ti", "vo", "zu", "de", "fi", "gu", "ha", "ne", "po", "yo"]

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.used: set[str] = set()

    def word(self) -> str:
        while True:
            w = "".join(self._SYL[int(i)] for i in self.rng.integers(0, len(self._SYL), size=3))
            if w not in self.used:
                self.used.add(w)
                return w

    def num(self, lo: int = 103, hi: int = 9899) -> int:
        small = hi - lo < 500  # 狭い範囲は一意性を諦める
        while True:
            n = int(self.rng.integers(lo, hi))
            if n % 10 and n not in (1, 2, 100) and (small or str(n) not in self.used):
                self.used.add(str(n))
                return n

    def real(self) -> str:
        while True:
            r = f"{int(self.rng.integers(3, 899))}.{int(self.rng.integers(11, 99))}"
            if r not in self.used and not r.endswith("0"):
                self.used.add(r)
                return r


def _csv(f: Fresh, sep: str, rows: int = 2, cols: int = 3, partial: bool = False) -> str:
    lines = [sep.join(f.word() for _ in range(cols))]
    for _ in range(rows):
        lines.append(sep.join(str(f.num()) if j else f.word() for j in range(cols)))
    if partial:  # 一部のセルだけが空の行（skip_blank で捨てられてはいけない）
        lines.append(sep.join(f.word() if j == 0 else ("" if j == 1 else str(f.num())) for j in range(cols)))
    return "\n".join(lines) + "\n"


def _rows(f: Fresh, cols: list[str], n: int = 3, blank: dict[int, dict[str, str]] | None = None) -> list[dict[str, str]]:
    out = [{c: (f.word() if k == 0 else str(f.num())) for k, c in enumerate(cols)} for _ in range(n)]
    for i, patch in (blank or {}).items():
        out[i].update(patch)
    return out


def _setup(rows: Any, name: str = "rows") -> tuple[str, ...]:
    return (f"{name} = {rows!r}",)


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    f = Fresh(rng)
    cs: list[dict[str, Any]] = []
    NL = "\n"
    W = f.word

    def N() -> str:
        return str(f.num())

    # --- detect_delimiter（18） ---
    for sep in [",", ";", "\t", "|"]:
        cs.append(_dd(f"detect_delimiter({_csv(f, sep)!r})", tags=("default",)))
    a, b, c, d = W(), W(), W(), W()
    cs += [
        _dd(f"detect_delimiter({'\"' + a + ',' + b + '\"' + ';' + c + NL + '\"' + d + ',' + a + '\"' + ';' + b + NL!r})", tags=("asym",)),
        _dd(f"detect_delimiter({a + ',' + b + NL + N() + ',' + N() + ',' + N() + NL!r}, ';|')", tags=("default",)),
        _dd("detect_delimiter('')", tags=("empty",)),
        _dd("detect_delimiter('', '|;')", tags=("empty",)),
        _dd(f"detect_delimiter({NL.join([a + ';' + b, '', '', N() + ';' + N()]) + NL!r})", tags=("empty",)),
        _dd(f"detect_delimiter({W() + ',' + W() + ';' + W() + NL + W() + ',' + W() + ';' + W() + NL!r})", tags=("tie",)),
        _dd(f"detect_delimiter({W() + ',' + W() + ';' + W() + NL + W() + ',' + W() + ';' + W() + NL!r}, ';,')", tags=("tie",)),
        _dd(f"detect_delimiter({W() + ':' + W() + NL + W() + ':' + W() + NL!r}, ':;')"),
    ]
    body20 = "".join(f"{W()};{W()}\n" for _ in range(20))
    body19 = "".join(f"{W()};{W()}\n" for _ in range(19))
    cs.append(_dd(f"detect_delimiter({body20 + W() + ',' + W() + ',' + W() + NL!r})", tags=("boundary",)))
    cs.append(_dd(f"detect_delimiter({body19 + W() + ',' + W() + ',' + W() + NL!r})", tags=("boundary",)))
    # --- read_rows（28） ---
    for sep in [",", ";", "\t", "|"]:
        cs.append(_rr(f"read_rows({_csv(f, sep, partial=True)!r})", tags=("default",)))
    h1, h2 = W(), W()
    w1, w2, w3 = W(), W(), W()
    Q = '"'
    cs += [
        _rr(r"""read_rows('name,note\n"widget","line one\nline two"\nbolt,plain\n')""", tags=("edge_task",)),
        _rr(f"read_rows({h1 + ',' + h2 + NL + Q + w1 + Q + Q + w2 + Q + Q + Q + ',' + Q + w3 + ',' + N() + Q + NL!r})"),
        _rr(f"read_rows({chr(0xFEFF) + _csv(f, ',', partial=True)!r})"),
        _rr(f"read_rows({chr(0xFEFF) + _csv(f, ';', partial=True)!r})"),
        _rr(f"read_rows({chr(0xFEFF) + _csv(f, ',')!r}, header=False)"),
        _rr(f"read_rows({_csv(f, ',', partial=True)!r}, delimiter=';')"),
        _rr(f"read_rows({_csv(f, ';')!r}, delimiter=';')"),
        _rr(f"read_rows({_csv(f, ',', partial=True)!r}, header=False)"),
    ]
    x1, x2 = W(), W()
    cs += [
        _rr(f"read_rows({x1 + ',' + x2 + NL + NL + '   ,  ' + NL + W() + ',' + N() + NL!r})", tags=("empty",)),
        _rr(f"read_rows({x1 + ',' + x2 + NL + NL + W() + ',' + N() + NL!r}, skip_blank=False)", tags=("empty",)),
        _rr(f"read_rows({x1 + ',' + x2 + NL + '  ,  ' + NL + W() + ',' + N() + NL!r}, skip_blank=False)"),
        _rr(f"read_rows({W() + ',' + W() + ',' + W() + NL + W() + ',' + N() + NL + W() + ',' + N() + ',' + N() + ',' + N() + NL!r})", tags=("boundary",)),
    ]
    d1, d2 = W(), W()
    cs += [
        _rr(f"read_rows({d1 + ',' + d1 + ',' + d1 + NL + N() + ',' + N() + ',' + N() + NL!r})"),
        _rr(f"read_rows({' ' + W() + ' , ' + W() + ' ' + NL + W() + ',' + N() + NL!r})"),
        _rr(f"read_rows({W().capitalize() + ' ' + W().capitalize() + ',' + W().capitalize() + 'ID' + NL + f.real() + ',' + W() + NL!r}, normalize=True)"),
        _rr(f"read_rows({'Unit Price,unit price' + NL + f.real() + ',' + f.real() + NL!r}, normalize=True)"),
        _rr("read_rows('')", tags=("empty",)),
        _rr(f"read_rows({W() + ',' + W() + NL!r})", tags=("empty",)),
        _rr(f"read_rows({W() + ',' + W() + chr(13) + NL + W() + ',' + N() + chr(13) + NL!r})"),
        _rr(f"read_rows({N() + ';' + N() + NL + N() + ';' + N() + NL!r}, header=False)"),
        _rr(f"read_rows({N() + ',' + N() + NL + NL + N() + ',' + N() + NL!r}, header=False, skip_blank=False)", tags=("empty",)),
    ]
    # --- coerce（33） ---
    ints = [N() for _ in range(12)]
    for text, kind in [
        (ints[0], "int"),
        (" " + ints[2] + " ", "int"),
        (ints[3] + ".0", "int"),
        (f"{f.num(3, 9)},{f.num(110, 990)}", "int"),
        ("(" + ints[5] + ")", "int"),
        (f"{f.num(3, 9)}00%", "int"),
        ("123456789012345678", "int"),
        (f.real(), "float"),
        (f"{f.num(3, 9)},{f.num(110, 990)}.5", "float"),
        (f"{f.real()}%", "float"),
        (f"{f.real()}%", "float"),
        (f"-{f.real()}%", "float"),
        (f"({f.real()})", "float"),
        (f"({f.real()})", "float"),
        (f"({f.num(3, 9)},{f.num(110, 990)})", "float"),
        ("-" + f.real(), "float"),
        ("Yes", "bool"),
        ("OFF", "bool"),
        (f" {W()} ", "str"),
    ]:
        cs.append(_co(f"coerce({text!r}, {kind!r})", tags=("boundary",) if text == "123456789012345678" else ()))
    for y, mo, dd in [(2024, 3, 17), (1900, 1, 1), (2999, 12, 31), (2023, 11, 5)]:
        yy = y if y in (1900, 2999) else int(rng.integers(1950, 2900))
        cs.append(_co(f"coerce({f'{yy}-{mo:02d}-{dd:02d}'!r}, 'date')", view="repr($)"))
    cs += [
        _co("coerce('', 'int')", tags=("empty",)),
        _co(f"coerce({chr(0xFEFF) + N()!r}, 'int')"),
        _co(f"coerce({chr(0xFEFF) + f.real()!r}, 'float')"),
        _co(f"coerce({chr(0xFEFF) + 'Yes'!r}, 'bool')"),
        _co(f"coerce({chr(0xFEFF) + '2024-05-17'!r}, 'date')", view="repr($)"),
        _co(f"coerce({f.num()}, 'float')"),
        _co(f"coerce({f.real()!r}, 'int')", raises="ValueError"),
        _co("coerce('9999999999999999999', 'int')", raises="ValueError", tags=("boundary",)),
        _co(f"coerce({W()!r}, 'bool')", raises="ValueError"),
        _co("coerce('2024-02-30', 'date')", raises="ValueError"),
        _co("coerce('1899-12-31', 'date')", raises="ValueError", tags=("boundary",)),
        _co("coerce('5', 'decimal')", raises="ValueError"),
    ]
    # --- apply_schema（15） ---
    def ap(cols: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _ap("apply_schema(rows, cols)", setup=(f"cols = {cols}", f"rows = {rows!r}"), **kw)

    n1, n2, n3 = W(), W(), W()
    cs.append(ap(f"[Column({n1!r}), Column({n2!r}, 'int'), Column({n3!r}, 'float')]", [{n1: W(), n2: N(), n3: f.real()}, {n1: W(), n2: N(), n3: f.real()}]))
    n1, n2, n3 = W(), W(), W()
    cs.append(ap(f"[Column({n1!r}), Column({n2!r}, 'int'), Column({n3!r}, 'float')]", [{n1: W(), n2: "", n3: " "}], tags=("empty",)))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, required=True), Column({n2!r}, 'int', required=True)]", [{n1: W(), n2: ""}, {n1: " ", n2: N()}]))
    n1, n2, n3 = W(), W(), W()
    cs.append(ap(f"[Column({n1!r}), Column({n2!r}, 'int'), Column({n3!r}, 'float')]", [{n1: W(), n2: "many", n3: f.real()}, {n1: W(), n2: f.real(), n3: "x1"}]))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'float'), Column({n2!r})]", [{n2: W(), n1: f.real(), W(): W()}]))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'int', default={f.num()}), Column({n2!r}, 'bool', default=False)]", [{n1: "", n2: ""}, {n1: N(), n2: "yes"}], tags=("default",)))
    n1 = W()
    cs.append(ap(f"[Column({n1!r}, 'int', required=True, default={f.num()})]", [{n1: ""}, {}]))
    n1 = W()
    cs.append(ap(f"[Column({n1!r}, 'int', required=True)]", [{W(): N()}]))
    cs.append(_ap("apply_schema([], cols)", setup=(f"cols = [Column({W()!r}, 'int')]",), tags=("empty",)))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'date'), Column({n2!r}, 'bool')]", [{n1: "2024-07-04", n2: "no"}, {n1: "2024-13-01", n2: W()}], view="str($)"))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'float'), Column({n2!r}, 'int')]", [{n1: f"{f.real()}%", n2: f"({f.num(3, 9)},{f.num(110, 990)})"}]))
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'int', required=True), Column({n2!r}, 'int', required=True)]", [{n1: W(), n2: ""}, {n1: N(), n2: N()}, {n1: "", n2: W()}]))
    n1 = W()
    n1, n2 = W(), W()
    cs.append(ap(f"[Column({n1!r}, 'bool'), Column({n2!r}, 'float', default={f.real()})]", [{n1: "on", n2: ""}, {n1: "off", n2: "(" + f.real() + ")"}]))
    n1, n2 = W(), W()
    # --- normalize_header（13） ---
    for name, extra in [
        ("Unit Price", ""),
        ("orderID", ""),
        (" Qty (kg) ", ""),
        ("売上", ""),
        ("", ""),
        ("Ünit Price", ""),
        ("abcdefghij" * 5, ""),
        ("Quarterly Revenue", ", 9"),
        ("Quarterly Revenue", ", 10"),
        ("Quarterly Revenue", ", 11"),
        ("Total  Net  Amount", ", 13"),
    ]:
        cs.append(_nh(f"normalize_header({name!r}{extra})", tags=("boundary",) if extra else ()))
    # --- select（8） ---
    def sel(cols_expr: str, names: list[str], **kw: Any) -> dict[str, Any]:
        return _tf("select", f"select(rows, {cols_expr})", setup=_setup(_rows(f, names, 2)), **kw)

    for _ in range(3):
        k = [W() for _ in range(3)]
        cs.append(sel(repr([k[2], k[0]]), k))
    k = [W() for _ in range(3)]
    cs.append(sel(repr([k[1]]), k[:2] + [W()], tags=("none",)))
    k = [W() for _ in range(2)]
    cs.append(sel(repr([k[0], W()]), k, tags=("none",)))
    cs.append(_tf("select", f"select([], [{W()!r}])", tags=("empty",)))
    k = [W() for _ in range(2)]
    cs.append(sel("[]", k, raises="ValueError", tags=("empty",)))
    k = [W() for _ in range(3)]
    cs.append(sel(repr(tuple(reversed(k))), k))
    # --- rename（11） ---
    def ren(call: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _tf("rename", call, setup=_setup(rows), **kw)

    def title() -> str:
        return W().capitalize() + " " + W().capitalize()

    for _ in range(3):
        q, r = W(), W()
        cs.append(ren(f"rename(rows, {{{q!r}: {title()!r}}})", [{q: W(), r: N()}]))
    for _ in range(3):
        t1, t2 = title(), title()
        cs.append(ren("rename(rows, normalize=True)", [{t1: W(), t2: N()}]))
    q = W()
    t1 = title()
    cs.append(ren(f"rename(rows, {{{q!r}: {title()!r}}}, normalize=True)", [{t1: f.real(), q: N()}]))
    for _ in range(2):
        q, r = W(), W()
        cs.append(ren("rename(rows)", [{q: W(), r: N()}], tags=("default",)))
    q, r = W(), W()
    cs.append(ren(f"rename(rows, {{{q!r}: {r!r}}})", [{q: W(), r: W()}], raises="ValueError"))
    t1 = title()
    cs.append(ren("rename(rows, normalize=True)", [{t1: f.real(), t1.lower(): f.real()}], raises="ValueError"))
    # --- filter_rows（28） ---
    def fr(call: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _tf("filter_rows", call, setup=_setup(rows), **kw)

    for op in ("lt", "le", "gt", "ge"):
        for _ in range(3):
            n = f.num(200, 8000)
            rows = [{"c": cell, "t": W()} for cell in [str(n - 5), str(n), str(n + 7), "", "junk"]]
            cs.append(fr(f"filter_rows(rows, 'c', {op!r}, {n})", rows, tags=("boundary",)))
        for _ in range(2):
            x = f.real()
            rows = [{"c": v, "t": W()} for v in [f"{float(x) - 0.5:.2f}", x, f"{float(x) + 0.5:.2f}", "(" + x + ")"]]
            cs.append(fr(f"filter_rows(rows, 'c', {op!r}, {x})", rows, tags=("boundary",)))
        w = W()
        rows = [{"c": v, "t": W()} for v in [W(), w, W(), ""]]
        cs.append(fr(f"filter_rows(rows, 'c', {op!r}, {w!r})", rows, tags=("boundary",)))
    n = f.num()
    rows = [{"c": v, "t": W()} for v in [str(n), f"{n},000", "", str(n + 1), "n/a"]]
    w = W()
    rows = [{"c": v, "t": W()} for v in [w, w.upper(), "", W()]]
    cs.append(fr(f"filter_rows(rows, 'c', 'eq', {w!r})", rows))
    rows = [{"c": v, "t": W()} for v in ["yes", "no", "maybe", " ", "ON", "1"]]
    cs.append(fr("filter_rows(rows, 'c', 'eq', True)", rows))
    w = W()
    rows = [{"c": w + W(), "t": W()}, {"c": W() + w[:2], "t": W()}, {"c": w.upper(), "t": W()}, {"c": "", "t": W()}]
    cs.append(fr(f"filter_rows(rows, 'c', 'contains', {w[1:5]!r})", rows))
    n = f.num(20, 90)
    rows = [{"c": f"x{n}y", "t": W()}, {"c": str(n + 5), "t": W()}, {"c": f"{n}", "t": W()}, {"c": "", "t": W()}]
    cs.append(fr(f"filter_rows(rows, 'c', 'contains', {n})", rows))
    for _ in range(3):
        cs.append(fr("filter_rows(rows, 'c', 'contains', None)", [{"c": W(), "t": W()}, {"c": "", "t": W()}, {"c": " ", "t": W()}]))
    cs.append(fr(f"filter_rows(rows, 'c', 'eq', {chr(0xFEFF) + str(f.num())!r})", [{"c": W()}]))
    cs.append(fr("filter_rows(rows, 'c', 'contains', True)", [{"c": f"is {W()} true", "t": W()}, {"c": f"{W()} True", "t": W()}]))
    n = f.num(300, 900)
    cs.append(fr(f"filter_rows(rows, 'c', 'gt', {n})", [{"c": f"{n},001"}, {"c": f"{n}"}, {"c": f"({n})"}, {"c": f"{n + 3}%"}]))
    # --- dedupe（11） ---
    def dd_(call: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _tf("dedupe", call, setup=_setup(rows), **kw)

    def drows() -> list[dict[str, str]]:
        i1, i2, g1, g2 = W(), W(), W(), W()
        return [
            {"id": i1, "g": g1, "v": N()},
            {"id": " " + i2, "g": g1, "v": N()},
            {"id": i1 + " ", "g": g2, "v": N()},
            {"id": "", "g": g2, "v": N()},
            {"id": "  ", "g": g1, "v": N()},
            {"id": i2, "g": g1, "v": N()},
            {"v": N()},
        ]

    for call in ["dedupe(rows, 'id')", "dedupe(rows, 'id')", "dedupe(rows, ['id', 'g'])", "dedupe(rows, ('g',))", "dedupe(rows, 'g')", "dedupe(rows, 'id', 'last')", "dedupe(rows, 'id', keep='last')", "dedupe(rows, ['id', 'g'], 'last')"]:
        cs.append(dd_(call, drows()))
    cs.append(dd_("dedupe([], 'id')", [], tags=("empty",)))
    cs.append(dd_("dedupe(rows, 'id', 'middle')", [{"id": W()}], raises="ValueError"))
    cs.append(dd_("dedupe(rows, [])", [{"id": W()}], raises="ValueError"))
    # --- group_by（19） ---
    def gb(call: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _gb(call, setup=_setup(rows), **kw)

    def grows(n: int = 8) -> list[dict[str, str]]:
        keys = [W(), " " + W(), "", W().capitalize()]
        out = []
        for i in range(n):
            amt = ["", f.real(), f"({f.real()})", f"{f.num(3, 9)},{f.num(110, 990)}", f"{f.real()}%", " "][i % 6]
            out.append({"k": keys[i % 4], "a": amt, "n": W() if i % 3 else ""})
        return out

    for call in [
        "group_by(rows, 'k', {'t': ('a', 'sum'), 'lo': ('a', 'min')})",
        "group_by(rows, 'k', {'t': ('a', 'sum'), 'hi': ('a', 'max')})",
        "group_by(rows, 'k', {'c': ('n', 'count')})",
        "group_by(rows, 'k', {'c': ('n', 'count')})",
        "group_by(rows, 'k', {'m': ('a', 'mean'), 'lo': ('a', 'min'), 'hi': ('a', 'max')})",
        "group_by(rows, 'k', {'m': ('a', 'mean'), 'lo': ('a', 'min'), 'hi': ('a', 'max')})",
        "group_by(rows, 'k', {'lo': ('a', 'min'), 'hi': ('a', 'max')})",
        "group_by(rows, 'k', {'lo': ('a', 'min')})",
        "group_by(rows, 'k', {'hi': ('a', 'max')})",
        "group_by(rows, 'k', {'t': ('a', 'sum'), 'c': ('a', 'count'), 'm': ('a', 'mean')})",
        "group_by(rows, ['k', 'n'], {'c': ('a', 'count')})",
    ]:
        cs.append(gb(call, grows()))
    cs.append(_gb("group_by([], 'k', {'t': ('a', 'sum')})", tags=("empty",)))
    k1 = W()
    cs.append(gb("group_by(rows, 'k', {'m': ('v', 'mean')})", [{"k": k1, "v": "1"}, {"k": k1, "v": "2"}, {"k": k1, "v": "2"}]))
    cs.append(gb("group_by(rows, 'k', {'c': ('v', 'count')})", [{"k": "10", "v": W()}, {"k": "9", "v": W()}, {"k": "B", "v": W()}, {"k": "a", "v": " "}]))
    k1 = W()
    cs.append(gb("group_by(rows, 'k', {'t': ('v', 'sum')})", [{"k": k1, "v": f"{f.num(3, 9)}0%"}, {"k": k1, "v": f"({f.real()})"}]))
    cs.append(gb("group_by(rows, 'k', {'x': ('v', 'median')})", [{"k": W(), "v": "1"}], raises="ValueError"))
    cs.append(gb("group_by(rows, 'k', {'t': ('v', 'sum')})", [{"k": W(), "v": W()}], raises="ValueError"))
    # --- write_rows（16） ---
    def wr(call: str, rows: Any, **kw: Any) -> dict[str, Any]:
        return _wr(call, setup=_setup(rows), **kw)

    i1, i2, c1 = W(), W(), W()
    base = [{i1: W(), i2: f.num(), c1: True}, {i1: f"{W()}, {W()}", i2: None, c1: False}]
    cs += [
        wr("write_rows(rows)", base),
        wr(f"write_rows(rows, [{i2!r}, {i1!r}])", base),
        wr(f"write_rows(rows, columns=[{i1!r}], quote_all=True)", base),
        wr("write_rows(rows, delimiter=';')", base),
        wr("write_rows(rows, delimiter='\\t')", base),
        wr("write_rows(rows)", [{"a": f'say "{W()}"', "b": f"{W()}\n{W()}"}]),
        wr("write_rows(rows)", [{"a": " " + W(), "b": W() + " ", "c": f"{W()} {W()}"}]),
        wr("write_rows(rows)", [{"a": f"{W()};{W()}", "b": f"{W()},{W()}"}]),
        wr("write_rows(rows, delimiter=';')", [{"a": f"{W()};{W()}", "b": f"{W()},{W()}"}]),
        wr("write_rows(rows)", [{"a": W()}, {"b": N()}, {"a": W(), "c": N()}]),
        _wr(f"write_rows([], [{W()!r}, {W()!r}])", tags=("empty",)),
        _wr("write_rows([])", tags=("empty",)),
        _wr("write_rows(rows)", setup=(f"rows = [{{'when': date(2024, {int(rng.integers(3, 9))}, {int(rng.integers(11, 28))}), 'n': {f.real()}}}]",)),
        wr(f"write_rows(rows, ['a,b', {i1!r}])", [{"a,b": W(), i1: W()}]),
        wr("write_rows(rows, quote_all=True, delimiter='|')", [{"a": f'{W()}"{W()}', "b": ""}]),
        wr(f"write_rows(rows, [{i1!r}, 'missing'])", [{i1: W()}], tags=("none",)),
        wr("write_rows(rows, delimiter='||')", [{"a": W()}], raises="ValueError"),
    ]
    return cs
