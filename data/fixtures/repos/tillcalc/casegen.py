"""tillcalc のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。

各ケースは「公開関数の 1 回の呼び出し」の式。期待値は正しいコードで計算して凍結する。
"""

from __future__ import annotations

from typing import Any

import numpy as np

PACKAGE = "tillcalc"
DESCRIPTION = "金額計算（丸め・按分・税・請求書）"
HEADER = ["import pytest", "from decimal import Decimal"]

TOPICS = {
    "Money.of": "金額の生成",
    "Money.from_minor": "最小単位からの金額の生成",
    "Money.to_minor": "最小単位への変換",
    "Money.__add__": "金額の足し算",
    "Money.__sub__": "金額の引き算",
    "Money.times": "金額の係数倍",
    "Money.ratio": "金額の比率計算",
    "Money.percent": "金額の百分率計算",
    "Money.line": "明細金額の計算",
    "Money.clamp": "金額の範囲への切り詰め",
    "Money.compare": "金額の大小比較",
    "Money.to_str": "金額の文字列化",
    "Money.format": "金額の表示",
    "allocate": "金額の按分",
    "split_even": "金額の等分",
    "add_tax": "税の計算",
    "tax_portion": "税込金額からの税額の取り出し",
    "progressive_tax": "累進課税",
    "marginal_rate": "限界税率",
    "Invoice.subtotal": "請求書の小計",
    "Invoice.tax_total": "請求書の消費税合計",
    "Invoice.grand_total": "請求書の税込合計",
    "round_to_step": "現金丸め",
}

# 紛らわしい名前・似た処理の組（変異の「囮あり」の判定に使う。キーは module.qualname）
DECOYS = [
    ["money._round_half", "rounding.half_up_div"],
    ["rounding.tax_part", "tax.tax_portion"],
    ["money.Money.ratio", "money.Money.times"],
    ["rounding._distribute_remainder", "rounding.largest_remainder"],
    ["money.Money.percent", "money.Money.ratio"],
]

README_REFS = {
    "Money.of": "最小単位より細かい端数は `mode` で丸める（既定は偶数丸め）",
    "Money.from_minor": "`Money.from_minor(n, code)` / `to_minor()`: 最小単位の個数",
    "Money.to_minor": "`Money.from_minor(n, code)` / `to_minor()`: 最小単位の個数",
    "Money.__add__": "`+` / `-`: 通貨が違えば `CurrencyError`",
    "Money.__sub__": "`+` / `-`: 通貨が違えば `CurrencyError`",
    "Money.times": "`times(factor, mode=None)`: 係数倍",
    "Money.ratio": "同点は 0 から遠い方へ丸める。`den` が 0 なら `ZeroDivisionError`",
    "Money.percent": "`percent(pct)`: `pct` パーセントの金額",
    "Money.line": "`Money.line(unit_price, qty, discount_pct=None)`: 単価 × 数量 − 値引き額",
    "Money.clamp": "`clamp(low, high)`: `low` 以上 `high` 以下に収める",
    "Money.compare": "`compare(other)`: `other` より小さければ -1、大きければ 1、等しければ 0",
    "Money.to_str": "`to_str()`: 通貨の桁数どおりの数値文字列",
    "Money.format": "桁区切りと通貨コード付き",
    "allocate": "端数は最小単位で最大剰余法により配り、剰余が同じなら前の要素を優先する",
    "split_even": "`split_even(total, n)`: `n` 等分。端数は先頭から 1 最小単位ずつ",
    "add_tax": "`inclusive=True` は `amount` を税込として税を取り出す",
    "tax_portion": "`tax_portion(gross, rate, mode=HALF_UP)`: 税込金額に含まれる税額",
    "progressive_tax": "各段には前の段の上限から自段の上限までの部分だけに税率を掛け",
    "marginal_rate": "`marginal_rate(income, brackets)`: 所得の最後の 1 単位に掛かる税率",
    "Invoice.subtotal": "`subtotal()`: 明細ごとに（単価 × 数量 − 値引き）を丸めて合計した税抜小計",
    "Invoice.tax_total": "`rounding=\"per_line\"` なら明細ごとに税を丸めた和",
    "Invoice.grand_total": "`grand_total()`: 小計 + 消費税",
    "round_to_step": "`step` の倍数のうち最も近いものに丸める（現金丸め",
}

EDGE = {
    "kind": "do_nothing",
    "tag": "edge_task",
    "readme_ref": "端数は先頭から 1 最小単位ずつ",
    "prompt": (
        "請求を 3 等分する処理で、100.00 USD を 3 人で割ると 1 セント消えて合計が 99.99 になるという報告が"
        "来ています。`split_even(Money.of('100.00', 'USD'), 3)` を呼ぶと割り切れない端数が失われる、とのことです。"
        "再現するか確認して、必要なら直してください。再現しない場合は、変更せずにその旨を報告してください。"
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


def _money(target: str, call: str, **kw: Any) -> dict[str, Any]:
    return _c(target, "money", call, view="$.to_str()", **kw)


def _list(target: str, module: str, call: str, **kw: Any) -> dict[str, Any]:
    return _c(target, module, call, view="[m.to_str() for m in $]", **kw)


def _m(value: str, code: str = "USD") -> str:
    return f"Money.of('{value}', '{code}')"


def _inv(lines: list[tuple[str, int, str, str | None]], rate: str, rounding: str, code: str = "USD") -> str:
    parts = []
    for desc, qty, price, pct in lines:
        disc = f", Decimal('{pct}')" if pct is not None else ""
        parts.append(f"Line('{desc}', {qty}, {_m(price, code)}{disc})")
    return f"Invoice('{code}', [{', '.join(parts)}], Decimal('{rate}'), '{rounding}')"


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    cs: list[dict[str, Any]] = []
    # --- Money.of ---
    for v, code, extra, tag in [
        ("10.005", "USD", "", "tie"),
        ("10.015", "USD", "", "tie"),
        ("2.5", "JPY", "", "tie"),
        ("3.5", "JPY", "", "tie"),
        ("0.5", "JPY", "", "tie"),
        ("10.005", "USD", ", RoundingMode.HALF_UP", "tie"),
        ("2.5", "JPY", ", RoundingMode.HALF_UP", "tie"),
        ("-2.5", "JPY", ", RoundingMode.HALF_UP", "tie"),
        ("12.349", "USD", ", RoundingMode.DOWN", ""),
        ("12.341", "USD", ", RoundingMode.UP", ""),
        ("7.0005", "KWD", "", "tie"),
    ]:
        cs.append(_money("Money.of", f"Money.of('{v}', '{code}'{extra})", tags=(tag,) if tag else ()))
    cs.append(_money("Money.of", "Money.of(1234, 'EUR')"))
    cs.append(_c("Money.of", "money", "Money.of(12.5, 'USD')", raises="TypeError", tags=("none",)))
    cs.append(_c("Money.of", "money", "Money.of('1', 'XXX')", raises="CurrencyError"))
    # --- from_minor / to_minor ---
    for n, code in [(1999, "USD"), (-4505, "EUR"), (7, "JPY"), (123456, "KWD"), (0, "USD")]:
        cs.append(_money("Money.from_minor", f"Money.from_minor({n}, '{code}')"))
    for v, code in [("19.99", "USD"), ("-45.05", "EUR"), ("1234", "JPY"), ("12.345", "KWD"), ("0.07", "USD")]:
        cs.append(_c("Money.to_minor", "money", f"{_m(v, code)}.to_minor()"))
    # --- add / sub ---
    for a, b, code in [("10.10", "2.25", "USD"), ("0.99", "0.01", "EUR"), ("-5", "12", "JPY"), ("1.234", "5.678", "KWD")]:
        cs.append(_money("Money.__add__", f"{_m(a, code)} + {_m(b, code)}"))
    cs.append(_c("Money.__add__", "money", f"{_m('1')} + {_m('1', 'EUR')}", raises="CurrencyError"))
    for a, b, code in [("10.10", "2.25", "USD"), ("2.25", "10.10", "USD"), ("100", "1", "JPY"), ("0.001", "0.003", "BHD")]:
        cs.append(_money("Money.__sub__", f"{_m(a, code)} - {_m(b, code)}", tags=("asym",)))
    cs.append(_c("Money.__sub__", "money", f"{_m('1', 'JPY')} - {_m('1')}", raises="CurrencyError"))
    # --- times ---
    for v, f, extra, code in [
        ("19.99", "'1.5'", "", "USD"),
        ("19.99", "3", "", "USD"),
        ("0.05", "'0.5'", "", "USD"),
        ("0.05", "'0.5'", ", RoundingMode.HALF_UP", "USD"),
        ("7.35", "'0.1'", "", "USD"),
        ("7.35", "'0.1'", ", RoundingMode.UP", "USD"),
        ("7.35", "'0.1'", ", RoundingMode.DOWN", "USD"),
        ("-33.33", "'0.15'", "", "EUR"),
        ("-33.33", "'0.15'", ", RoundingMode.HALF_UP", "EUR"),
        ("1250", "'0.075'", "", "JPY"),
        ("1250", "'0.075'", ", RoundingMode.HALF_UP", "JPY"),
        ("48.125", "Decimal('2')", "", "KWD"),
        ("500", "'1.0005'", ", RoundingMode.UP", "JPY"),
    ]:
        cs.append(_money("Money.times", f"{_m(v, code)}.times({f}{extra})", tags=("default",) if not extra else ()))
    for v, f in [("0.05", "'0.5'"), ("0.15", "'0.5'"), ("0.25", "'0.5'"), ("2.50", "'0.1'"), ("1.25", "'0.1'"), ("5", "'0.5'")]:
        code = "JPY" if v == "5" else "USD"
        cs.append(_money("Money.times", f"{_m(v, code)}.times({f})", tags=("tie", "default")))
    # --- ratio ---
    for v, n, d, code, tag in [
        ("10.00", 1, 3, "USD", ""),
        ("10.01", 1, 2, "USD", "tie"),
        ("0.05", 1, 2, "USD", "tie"),
        ("-0.05", 1, 2, "USD", "tie"),
        ("100", 1, 3, "JPY", ""),
        ("101", 1, 2, "JPY", "tie"),
        ("-101", 1, 2, "JPY", "tie"),
        ("12.34", 5, 4, "USD", "asym"),
        ("12.34", 4, 5, "USD", "asym"),
        ("1.001", 2, 3, "KWD", ""),
    ]:
        cs.append(_money("Money.ratio", f"{_m(v, code)}.ratio({n}, {d})", tags=(tag,) if tag else ()))
    cs.append(_c("Money.ratio", "money", f"{_m('5')}.ratio(1, 0)", raises="ZeroDivisionError"))
    # --- percent ---
    for v, pct, code in [
        ("200.00", "15", "USD"),
        ("0.30", "50", "USD"),
        ("0.70", "50", "USD"),
        ("-0.70", "50", "USD"),
        ("1999", "'12.5'", "JPY"),
        ("1999", "'12.4'", "JPY"),
        ("33.33", "'7.5'", "EUR"),
        ("5.000", "33", "KWD"),
    ]:
        cs.append(_money("Money.percent", f"{_m(v, code)}.percent({pct})"))
    # --- line / clamp / compare ---
    for price, qty, pct, code in [
        ("9.99", 3, "None", "USD"),
        ("9.99", 3, "'15'", "USD"),
        ("0.35", 7, "'12.5'", "USD"),
        ("398", 4, "'5'", "JPY"),
        ("1280", 1, "'12.5'", "JPY"),
        ("0.015", 9, "'50'", "KWD"),
        ("45.45", 3, "'33.3'", "EUR"),
        ("45.45", 1, "0", "EUR"),
        ("115", 7, "None", "JPY"),
        ("0.33", 3, "'7'", "USD"),
    ]:
        cs.append(_money("Money.line", f"Money.line({_m(price, code)}, {qty}, {pct})"))
    for v, lo, hi, code in [
        ("5.55", "1.25", "9.75", "USD"),
        ("0.55", "1.25", "9.75", "USD"),
        ("19.55", "1.25", "9.75", "USD"),
        ("1.25", "1.25", "9.75", "USD"),
        ("9.75", "1.25", "9.75", "USD"),
        ("-300", "-250", "-40", "JPY"),
        ("7.123", "7.123", "7.123", "KWD"),
    ]:
        cs.append(_money("Money.clamp", f"{_m(v, code)}.clamp({_m(lo, code)}, {_m(hi, code)})"))
    cs.append(_c("Money.clamp", "money", f"{_m('5')}.clamp({_m('9')}, {_m('3')})", raises="CurrencyError"))
    for a, b, code in [("5.55", "6.66", "USD"), ("6.66", "5.55", "USD"), ("7.77", "7.77", "USD"), ("-3", "-30", "JPY"), ("0.001", "0.002", "KWD")]:
        cs.append(_c("Money.compare", "money", f"{_m(a, code)}.compare({_m(b, code)})"))
    cs.append(_c("Money.compare", "money", f"{_m('1')}.compare({_m('1', 'EUR')})", raises="CurrencyError"))
    # --- to_str / format ---
    for v, code in [("5", "USD"), ("0.5", "USD"), ("-12.5", "EUR"), ("1234", "JPY")]:
        cs.append(_c("Money.to_str", "money", f"{_m(v, code)}.to_str()"))
    for v, code in [("1234.5", "USD"), ("-1234567.891", "KWD"), ("999", "JPY"), ("1000", "JPY"), ("-5000", "JPY")]:
        cs.append(_c("Money.format", "money", f"{_m(v, code)}.format()"))
    # --- allocate ---
    fixed = [
        ("100.00", "USD", [1, 1, 1]),
        ("100", "JPY", [1, 1, 1]),
        ("0.05", "USD", [3, 7]),
        ("10.01", "USD", [1, 1]),
        ("1000", "JPY", [5, 3, 2]),
        ("-100.00", "USD", [1, 1, 1]),
        ("37.45", "EUR", [2, 3, 5]),
        ("12.345", "KWD", [7, 2, 1]),
    ]
    for v, code, ratios in fixed:
        cs.append(_list("allocate", "allocate", f"allocate({_m(v, code)}, {ratios})", tags=("tie",) if len(set(ratios)) == 1 else ()))
    for _ in range(2):
        cents = int(rng.integers(1000, 99999))
        ratios = [int(x) for x in rng.integers(1, 9, size=int(rng.integers(2, 5)))]
        cs.append(_list("allocate", "allocate", f"allocate({_m(f'{cents // 100}.{cents % 100:02d}')}, {ratios})"))
    cs.append(_c("allocate", "allocate", f"allocate({_m('10')}, [])", raises="CurrencyError", tags=("empty",)))
    cs.append(_c("allocate", "allocate", f"allocate({_m('10')}, [0, 0])", raises="CurrencyError"))
    cs.append(_c("allocate", "allocate", f"allocate({_m('10')}, [1, -1])", raises="CurrencyError"))
    # --- split_even ---
    for v, code, n in [
        ("100.00", "USD", 3),
        ("100", "JPY", 3),
        ("0.01", "USD", 4),
        ("-10.00", "EUR", 3),
        ("20.00", "USD", 4),
        ("7.77", "USD", 1),
        ("5.003", "KWD", 3),
        ("1000", "JPY", 7),
        ("33.34", "GBP", 5),
    ]:
        cs.append(_list("split_even", "allocate", f"split_even({_m(v, code)}, {n})"))
    cs.append(_c("split_even", "allocate", f"split_even({_m('10')}, 0)", raises="CurrencyError", tags=("boundary",)))
    # --- add_tax ---
    fixed_tax = [
        ("100.05", "'0.10'", "", "USD"),
        ("100.04", "'0.10'", "", "USD"),
        ("19.99", "'0.08'", "", "USD"),
        ("1000", "'0.10'", "", "JPY"),
        ("1005", "'0.10'", "", "JPY"),
        ("110.00", "'0.10'", ", True", "USD"),
        ("108", "'0.08'", ", True", "JPY"),
        ("107.99", "'0.07'", ", True", "EUR"),
        ("55.55", "'0.2'", ", True", "USD"),
        ("55.55", "'0.2'", ", False, RoundingMode.DOWN", "USD"),
        ("25.25", "'0.1'", ", False, RoundingMode.UP", "USD"),
        ("25.25", "'0.1'", ", False, RoundingMode.HALF_EVEN", "USD"),
        ("0.25", "'0.1'", ", False, RoundingMode.HALF_EVEN", "USD"),
        ("33.33", "0", "", "USD"),
        ("12.50", "'0.19'", ", True, RoundingMode.DOWN", "EUR"),
    ]
    for v, r, extra, code in fixed_tax:
        cs.append(_list("add_tax", "tax", f"add_tax({_m(v, code)}, {r}{extra})"))
    cs.append(_c("add_tax", "tax", f"add_tax({_m('10')}, '-0.1')", raises="CurrencyError"))
    # --- tax_portion ---
    for v, r, extra, code in [
        ("110.00", "'0.10'", "", "USD"),
        ("105.00", "'0.05'", "", "USD"),
        ("1000", "'0.08'", "", "JPY"),
        ("1001", "'0.08'", "", "JPY"),
        ("99.99", "'0.2'", "", "EUR"),
        ("99.99", "'0.2'", ", RoundingMode.DOWN", "EUR"),
        ("99.99", "'0.2'", ", RoundingMode.UP", "EUR"),
        ("0.37", "'0.1'", "", "USD"),
        ("3.10", "'0.55'", ", RoundingMode.HALF_EVEN", "USD"),
    ]:
        cs.append(_money("tax_portion", f"tax_portion({_m(v, code)}, {r}{extra})").copy() | {"module": "tax"})
    # --- progressive_tax（段の区切り・税率・通貨を毎回変えて、ケース間でリテラルを共有しない） ---
    codes = ["JPY", "KRW", "VND", "USD", "EUR", "GBP", "AUD", "CHF", "KWD"]
    specs: list[tuple[str, str, str]] = []
    for i, code in enumerate(codes):
        a = int(rng.integers(11, 90)) * 137 + i
        b = a + int(rng.integers(21, 80)) * 211 + i
        r1, r2, r3 = (f"0.{int(rng.integers(11, 20))}{i}", f"0.{int(rng.integers(21, 30))}{i}", f"0.{int(rng.integers(31, 45))}{i}")
        brk = f"[({a}, '{r1}'), ({b}, '{r2}'), (None, '{r3}')]"
        inc = [a - 53, a, a + 61, b + 1777, b, (a + b) // 2, 0, b - 1, a + 1][i]
        specs.append((str(inc), brk, code))
    for v, brk, code in specs:
        cs.append(_money("progressive_tax", f"progressive_tax({_m(v, code)}, {brk})").copy() | {"module": "tax"})
    cs.append(_c("progressive_tax", "tax", f"progressive_tax({_m('100')}, [(50, '0.1'), (20, '0.2')])", raises="CurrencyError"))
    # --- marginal_rate ---
    for i, (v, brk, code) in enumerate(specs[:8]):
        inc2 = str(int(v) + (i * 7919) % 313)
        cs.append(_c("marginal_rate", "tax", f"marginal_rate({_m(inc2, codes[(i + 3) % 9])}, {brk})", view="str($)"))
    # --- Invoice ---
    invs = {
        "A": _inv([("pen", 3, "1.25", None), ("ink", 2, "7.99", None)], "0.08", "per_line"),
        "B": _inv([("pen", 3, "1.25", None), ("ink", 2, "7.99", None)], "0.08", "per_invoice"),
        "C": _inv([("cup", 3, "9.99", "15"), ("lid", 1, "0.35", None)], "0.10", "per_line"),
        "D": _inv([("cup", 3, "9.99", "15"), ("lid", 1, "0.35", None)], "0.10", "per_invoice"),
        "E": _inv([("rice", 4, "398", "5"), ("tea", 7, "115", None), ("jam", 1, "1280", "12.5")], "0.08", "per_line", "JPY"),
        "J": "Invoice('EUR', [], Decimal('0.19'))",
        "K": "Invoice('USD', [Line('odd', 1, Money.of('5', 'EUR'))], Decimal('0.1'))",
    }
    for name, expr in invs.items():
        for target in ("Invoice.subtotal", "Invoice.tax_total", "Invoice.grand_total"):
            meth = target.split(".")[1]
            if name == "K":
                if meth == "subtotal":
                    cs.append(_c(target, "invoice", "inv.subtotal()", setup=(f"inv = {expr}",), raises="CurrencyError"))
                continue
            if name in "BD" and meth == "subtotal":
                continue  # per_invoice は小計が同じなので重複を避ける
            tags = ("empty",) if name == "J" else ()
            cs.append(_money(target, f"inv.{meth}()", setup=(f"inv = {expr}",), tags=tags).copy() | {"module": "invoice"})
    # --- round_to_step ---
    for v, step in [
        ("12.37", "'0.05'"),
        ("12.375", "'0.05'"),
        ("12.325", "'0.05'"),
        ("-12.375", "'0.05'"),
        ("1234", "10"),
        ("1235", "10"),
        ("1234", "'100'"),
        ("0.12", "'0.25'"),
        ("0.125", "'0.25'"),
    ]:
        cs.append(_c("round_to_step", "rounding", f"round_to_step('{v}', {step})", view="str($)"))
    cs.append(_c("round_to_step", "rounding", "round_to_step(15, 5)", view="str($)", tags=("boundary",)))
    cs.append(_c("round_to_step", "rounding", "round_to_step('1', 0)", raises="CurrencyError", tags=("boundary",)))
    # --- 編集タスク用（edge_task）: 3 等分で端数が消えるという誤報 ---
    cs.append(_list("split_even", "allocate", f"split_even({_m('100.00')}, 3)", tags=("edge_task",)))
    return cs
