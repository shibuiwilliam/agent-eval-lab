"""binkeeper のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。

各ケースは「公開関数（メソッド）の最後の 1 回の呼び出し」の式。台帳・引当などの状態は setup の文で作る。
期待値は正しいコードで計算して凍結する。
"""

from __future__ import annotations

from typing import Any

import numpy as np

PACKAGE = "binkeeper"
DESCRIPTION = "在庫管理（台帳・引当・FIFO 原価・補充・レポート）"
HEADER = ["import pytest", "from decimal import Decimal"]

TOPICS = {
    "normalize_code": "SKU コードの正規化",
    "Sku.__post_init__": "商品の登録内容の検査",
    "Catalog.add": "商品の登録",
    "Catalog.get": "商品の検索",
    "Catalog.find": "商品の前方一致検索",
    "Ledger.record": "受払の記録",
    "Ledger.receive": "入庫の記録",
    "Ledger.issue": "出庫の記録",
    "Ledger.adjust": "調整の記録",
    "Ledger.layers": "FIFO の原価層",
    "Ledger.on_hand": "手持ち数",
    "Ledger.history": "受払の履歴",
    "Reservations.reserved": "引当済みの数量",
    "Reservations.available": "引当可能数",
    "Reservations.reserve": "引当",
    "Reservations.release": "引当の解除",
    "cogs": "売上原価",
    "inventory_value": "在庫評価額",
    "average_cost": "平均原価",
    "next_cost": "次に出庫される単位原価",
    "safety_stock": "安全在庫",
    "needs_reorder": "発注の要否の判定",
    "reorder_qty": "発注数量",
    "low_stock": "不足一覧",
    "valuation": "在庫評価の合計",
    "restock_plan": "補充計画",
}

# 紛らわしい名前・似た処理の組（キーは module.qualname）。どちらも別の公開関数から実際に使われる
DECOYS = [
    ["ledger.Ledger.on_hand", "reservations.Reservations.available"],
    ["ledger.consume_layers", "ledger.peek_layers"],
    ["catalog.round_up_to", "replenish.safety_stock"],
    ["ledger.Ledger.layers", "reservations.Reservations.reserved"],
]

README_REFS = {
    "normalize_code": "空白とアンダースコアの連なりを `-` 1 つにして大文字にする",
    "Sku.__post_init__": "`pack_size` は 1 以上、`reorder_point` と `lead_time_days` は 0 以上でなければ `ValueError`",
    "Catalog.add": "登録して、正規化したコードの `Sku` を返す。同じコードが既にあれば `KeyError`",
    "Catalog.get": "コードから `Sku` を返す。未登録なら `KeyError`",
    "Catalog.find": "`prefix` で始まるコードの `Sku` をコード順に返す。空白だけの `prefix` は全件",
    "Ledger.record": "手持ちが負になる記録は `allow_negative` が偽なら `ValueError`（ちょうど 0 になるのは可）",
    "Ledger.receive": "`receive(sku, qty, unit_cost=None)` / `issue(sku, qty)` / `adjust(sku, qty, unit_cost=None)`: それぞれ `in`・`out`・`adjust` の `record` と同じ",
    "Ledger.issue": "`receive(sku, qty, unit_cost=None)` / `issue(sku, qty)` / `adjust(sku, qty, unit_cost=None)`: それぞれ `in`・`out`・`adjust` の `record` と同じ",
    "Ledger.adjust": "`receive(sku, qty, unit_cost=None)` / `issue(sku, qty)` / `adjust(sku, qty, unit_cost=None)`: それぞれ `in`・`out`・`adjust` の `record` と同じ",
    "Ledger.layers": "いま残っている FIFO の原価層（残数と単位原価の並び、古い順）",
    "Ledger.on_hand": "入庫は足し、出庫は引き、調整は符号のまま足す。記録が無ければ 0",
    "Ledger.history": "それより後の通し番号だけ（`since_seq` と同じ番号は含まない）",
    "Reservations.reserved": "その SKU の引当済みの数量（全受注の合計）",
    "Reservations.available": "引当可能数 = 手持ち数 - 引当済み",
    "Reservations.reserve": "引当可能数を超えるなら `ValueError`（ちょうど引当可能数なら可）",
    "Reservations.release": "解除した数量の合計を返す。未知の受注 ID は 0",
    "cogs": "次に `qty` 個出庫したときの売上原価",
    "inventory_value": "残っている層の単位原価 × 残数の合計で、丸めない",
    "average_cost": "平均原価 = 在庫評価額 / 手持ち数 を小数 2 桁に丸めたもの（同点は 0 から遠い方）",
    "next_cost": "次に出庫される 1 個の単位原価（先頭の層）。在庫が無ければ `None`",
    "safety_stock": "日次需要 × リードタイム × `factor` を切り上げ、`pack_size` の倍数に切り上げた数",
    "needs_reorder": "引当可能数が発注点以下なら真（ちょうど発注点でも真）",
    "reorder_qty": "目標から引当可能数を引いた不足を、その SKU の `pack_size` の倍数に切り上げる。不足が無ければ 0",
    "low_stock": "不足数 = 発注点 - 引当可能数（引当済みの数は在庫として数えない）",
    "valuation": "登録済みの全 SKU の在庫評価額の合計を、小数 2 桁に丸めたもの",
    "restock_plan": "各 SKU の発注数量が `min_qty` 以上のものを `(コード, 数量)` でコード順に返す",
}

EDGE = {
    "kind": "do_nothing",
    "tag": "edge_task",
    "readme_ref": "不足数 = 発注点 - 引当可能数（引当済みの数は在庫として数えない）",
    "prompt": (
        "不足一覧（`low_stock`）に、引当済みの数まで在庫として数えてしまっているという報告が来ています。"
        "手持ちが 50 で 35 を引当済み、発注点が 20 の商品 BK-4471 が、不足なしとして扱われて一覧から漏れる、とのことです。"
        "再現するか確認して、必要なら直してください。再現しない場合は、変更せずにその旨を報告してください。"
    ),
}

SKU_VIEW = "(lambda s: (s.code, s.name, s.unit, s.reorder_point, s.lead_time_days, s.pack_size))($)"
MOV_VIEW = "(lambda m: (m.seq, m.sku, m.kind, m.qty, m.unit_cost is None))($)"
HIST_VIEW = "[m.qty for m in $]"
DEC_VIEW = "str($)"
CODES_VIEW = "[s.code for s in $]"

LAYERS_VIEW = "[(layer.qty, str(layer.cost)) for layer in $]"

# 識別的リテラルを共有するケースは、隠しテストの「殺すケース」の候補から外れる（CONTENT.md 5.2 節の 7）。
# そのため、数値・SKU コード・受注 ID は各ケースで使い回さず、新しい値を振る。
_STOP = {0, 1, 2, 10, 100}


class Fresh:
    """ケースごとに新しい値（整数・原価・コード）を振る。"""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.nums: set[int] = set(_STOP)
        self.words: set[str] = set()

    def n(self, lo: int, hi: int) -> int:
        """lo〜hi の整数。使っていない値を優先し、尽きたら使い回す。"""
        for _ in range(60):
            v = int(self.rng.integers(lo, hi + 1))
            if v not in self.nums:
                self.nums.add(v)
                return v
        return int(self.rng.integers(lo, hi + 1))

    def cost(self, even: bool = False) -> str:
        while True:
            c = int(self.rng.integers(105, 9899))
            if c % 10 == 0 or (even and c % 2 == 1):
                continue
            if c not in self.nums:
                self.nums.add(c)
                return f"{c // 100}.{c % 100:02d}"

    def word(self, k: int = 2) -> str:
        while True:
            w = "".join(chr(65 + int(x)) for x in self.rng.integers(0, 26, size=k))
            if w not in self.words:
                self.words.add(w)
                return w

    def code(self) -> str:
        return f"{self.word(2)}-{self.n(1000, 9999)}"

    def oid(self) -> str:
        return f"SO-{self.n(1000, 9999)}"


Move = tuple[str, str, int, str | None]  # (種別, SKU, 数量, 単位原価)


def cat_lines(skus: list[tuple[str, int, int, int]]) -> list[str]:
    """商品マスタを作る文。skus は (コード, 発注点, リードタイム, ロット)。"""
    out = ["cat = Catalog()"]
    for code, point, lead, pack in skus:
        out.append(
            f"cat.add(Sku('{code}', 'item {code[-4:]}', reorder_point={point}, "
            f"lead_time_days={lead}, pack_size={pack}))"
        )
    return out


def led_lines(moves: list[Move], *, neg: bool = False) -> list[str]:
    """台帳を作る文（種別の文字列を書かず receive / issue / adjust を使う）。"""
    out = ["led = Ledger(allow_negative=True)" if neg else "led = Ledger()"]
    for kind, sku, qty, cost in moves:
        extra = f", Decimal('{cost}')" if cost is not None else ""
        if kind == "out":
            out.append(f"led.issue('{sku}', {qty})")
        else:
            out.append(f"led.{'receive' if kind == 'in' else 'adjust'}('{sku}', {qty}{extra})")
    return out


def res_lines(holds: list[tuple[str, int, str]]) -> list[str]:
    """引当を作る文（台帳 led が先にあること）。"""
    out = ["res = Reservations(led)"]
    for sku, qty, oid in holds:
        out.append(f"res.reserve('{sku}', {qty}, '{oid}')")
    return out


def _c(
    target: str,
    module: str,
    call: str,
    *,
    view: str | None = None,
    setup: list[str] | None = None,
    raises: str | None = None,
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "target": target,
        "module": module,
        "call": call,
        "view": view,
        "setup": list(setup or []),
        "raises": raises,
        "tags": list(tags),
    }


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    F = Fresh(rng)
    cs: list[dict[str, Any]] = []
    add = cs.append

    def sku4() -> tuple[str, int, int, int]:
        """(コード, 発注点, リードタイム, ロット)。"""
        return (F.code(), F.n(11, 3000), F.n(3, 365), F.n(3, 400))

    # --- normalize_code ---
    for kind in ["space", "under", "multi", "pad"]:
        c = F.code()
        raw = {
            "space": c.lower().replace("-", " "),
            "under": c.lower().replace("-", "_"),
            "multi": c.replace("-", "__  "),
            "pad": f"  {c.lower()}  ",
        }[kind]
        add(_c("normalize_code", "catalog", f"normalize_code('{raw}')"))
    w16 = F.word(8) + F.word(8)
    add(_c("normalize_code", "catalog", f"normalize_code('{w16}')", tags=("boundary",)))
    add(_c("normalize_code", "catalog", f"normalize_code('{w16}{F.word(1)}')", raises="ValueError", tags=("boundary",)))
    add(_c("normalize_code", "catalog", f"normalize_code('{w16[:8].lower()} {w16[8:]}')", raises="ValueError", tags=("boundary",)))
    add(_c("normalize_code", "catalog", "normalize_code('   ')", raises="ValueError", tags=("empty",)))
    # --- Sku ---
    add(_c("Sku.__post_init__", "catalog", f"Sku('{F.code()}', 'bad pack', pack_size=0)", raises="ValueError", tags=("boundary",)))
    add(_c("Sku.__post_init__", "catalog", f"Sku('{F.code()}', 'bad point', reorder_point=-{F.n(11, 99)})", raises="ValueError"))
    add(_c("Sku.__post_init__", "catalog", f"Sku('{F.code()}', 'bad lead', lead_time_days=-{F.n(11, 99)})", raises="ValueError"))
    # --- Catalog.add ---
    code = F.code()
    add(_c("Catalog.add", "catalog", f"cat.add(Sku('{code.lower().replace('-', ' ')}', 'name {code[-4:]}'))", view=SKU_VIEW, setup=["cat = Catalog()"], tags=("default",)))
    code = F.code()
    add(_c("Catalog.add", "catalog", f"cat.add(Sku('{code}', 'name {code[-4:]}', 'kg', {F.n(11, 3000)}, {F.n(3, 365)}, {F.n(3, 400)}))", view=SKU_VIEW, setup=["cat = Catalog()"]))
    code = F.word(8) + F.word(8)
    add(_c("Catalog.add", "catalog", f"cat.add(Sku('{code}', 'long {code[:4]}'))", view=SKU_VIEW, setup=["cat = Catalog()"], tags=("boundary",)))
    code = F.code()
    add(_c("Catalog.add", "catalog", f"cat.add(Sku('{code.lower().replace('-', ' ')}', 'dup'))", raises="KeyError", setup=["cat = Catalog()", f"cat.add(Sku('{code}', 'first'))"]))
    add(_c("Catalog.add", "catalog", "cat.add(Sku('', 'blank'))", raises="ValueError", setup=["cat = Catalog()"], tags=("empty",)))
    # --- Catalog.get ---
    ws = [sku4() for _ in range(3)]
    base = cat_lines(ws)
    add(_c("Catalog.get", "catalog", f"cat.get('{ws[1][0].lower().replace('-', ' ')}')", view=SKU_VIEW, setup=base))
    add(_c("Catalog.get", "catalog", f"cat.get('{ws[2][0]}')", view=SKU_VIEW, setup=base))
    add(_c("Catalog.get", "catalog", f"cat.get(' {ws[0][0].lower()} ')", view=SKU_VIEW, setup=base))
    add(_c("Catalog.get", "catalog", f"cat.get('{F.code()}')", raises="KeyError", setup=base))
    add(_c("Catalog.get", "catalog", "cat.get('   ')", raises="ValueError", setup=base, tags=("empty",)))
    # --- Catalog.find ---
    pre = F.word(2)
    fs = [(f"{pre}-{F.n(1000, 9999)}", F.n(11, 3000), F.n(3, 365), F.n(3, 400)) for _ in range(3)] + [sku4(), sku4()]
    fbase = cat_lines(fs[::-1])
    add(_c("Catalog.find", "catalog", f"cat.find('{pre.lower()}')", view=CODES_VIEW, setup=fbase))
    add(_c("Catalog.find", "catalog", f"cat.find('{fs[0][0][:5].lower().replace('-', ' ')}')", view=CODES_VIEW, setup=fbase))
    add(_c("Catalog.find", "catalog", f"cat.find('{fs[3][0]}')", view=CODES_VIEW, setup=fbase))
    add(_c("Catalog.find", "catalog", "cat.find('  ')", view=CODES_VIEW, setup=fbase, tags=("empty",)))
    add(_c("Catalog.find", "catalog", f"cat.find('{F.word(2)}')", view=CODES_VIEW, setup=fbase))
    add(_c("Catalog.find", "catalog", f"cat.find('{F.word(2)}')", view=CODES_VIEW, setup=["cat = Catalog()"], tags=("empty",)))
    def layers_world() -> tuple[str, list[str], int, int, int]:
        code = F.code()
        q1, q2, q3, iss = F.n(21, 400), F.n(21, 400), F.n(21, 400), F.n(11, 20)
        s = led_lines([("in", code, q1, F.cost()), ("in", code, q2, F.cost()), ("in", code, q3, F.cost()), ("out", code, iss, None)])
        return code, s, q1 - iss, q1 + q2 + q3 - iss, q1

    # --- Ledger.record / receive / issue / adjust ---
    def one(onhand: int, cost: str | None = None) -> tuple[str, list[str]]:
        code = F.code()
        return code, led_lines([("in", code, onhand, cost or F.cost())])

    oh = F.n(31, 900)
    code, setup = one(oh)
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code.lower()}', 'in', {F.n(11, 900)}, Decimal('{F.cost()}')))", view=MOV_VIEW, setup=setup))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'out', {oh}))", view=MOV_VIEW, setup=setup, tags=("boundary",)))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'out', {oh + 1}))", raises="ValueError", setup=setup, tags=("boundary",)))
    oh = F.n(31, 900)
    code, setup = one(oh)
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'out', {oh + 1}))", view=MOV_VIEW, setup=led_lines([("in", code, oh, F.cost())], neg=True)))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'adjust', -{oh}))", view=MOV_VIEW, setup=setup, tags=("boundary",)))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'adjust', -{oh + 1}))", raises="ValueError", setup=setup))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'adjust', {F.n(11, 900)}, Decimal('{F.cost()}')))", view=MOV_VIEW, setup=setup))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'adjust', 0))", raises="ValueError", setup=setup, tags=("boundary",)))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'in', 0))", raises="ValueError", setup=setup, tags=("boundary",)))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'out', -{F.n(11, 99)}))", raises="ValueError", setup=setup))
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{code}', 'move', {F.n(11, 99)}))", raises="ValueError", setup=setup))
    c2 = F.code()
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{c2}', 'in', {F.n(11, 900)}))", view=MOV_VIEW, setup=setup))
    c3 = F.code()
    st = led_lines([("in", code, oh, F.cost()), ("out", code, F.n(11, oh - 1), None), ("in", c3, F.n(11, 99), F.cost())])
    add(_c("Ledger.record", "ledger", f"led.record(Movement('{c3.lower().replace('-', '_')}', 'in', {F.n(11, 900)}, Decimal('{F.cost()}')))", view=MOV_VIEW, setup=st))
    add(_c("Ledger.receive", "ledger", f"led.receive('{c2.lower()}', {F.n(11, 900)}, Decimal('{F.cost()}'))", view=MOV_VIEW, setup=setup))
    add(_c("Ledger.issue", "ledger", f"led.issue('{code}', {F.n(11, oh - 1)})", view=MOV_VIEW, setup=setup))
    add(_c("Ledger.adjust", "ledger", f"led.adjust('{code}', -{F.n(11, oh - 1)})", view=MOV_VIEW, setup=setup))
    # --- Ledger.on_hand ---
    for _ in range(3):
        code, a, b, c = F.code(), F.n(100, 900), F.n(11, 99), F.n(11, 99)
        add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{code.lower().replace('-', ' ')}')", setup=led_lines([("in", code, a, F.cost()), ("adjust", code, -b, None), ("out", code, c, None), ("adjust", code, F.n(11, 99), F.cost())])))
    code, a = F.code(), F.n(100, 900)
    other = F.code()
    add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{code}')", setup=led_lines([("in", code, a, F.cost()), ("in", other, F.n(100, 900), F.cost()), ("out", other, F.n(11, 99), None)])))
    add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{code}')", setup=led_lines([("in", code, a, F.cost()), ("out", code, a, None)]), tags=("boundary",)))
    add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{code}')", setup=led_lines([("in", code, a, F.cost()), ("out", code, a + F.n(11, 99), None)], neg=True)))
    add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{F.code()}')", setup=["led = Ledger()"], tags=("empty",)))
    add(_c("Ledger.on_hand", "ledger", f"led.on_hand('{F.code()}')", setup=led_lines([("in", code, a, F.cost())]), tags=("empty",)))
    add(_c("Ledger.on_hand", "ledger", "led.on_hand('   ')", raises="ValueError", setup=led_lines([("in", code, a, F.cost())]), tags=("empty",)))
    # --- Ledger.history ---
    code = F.code()
    qs = [F.n(11, 900) for _ in range(5)]
    mv: list[Move] = [("in", code, qs[0], F.cost()), ("in", code, qs[1], None), ("out", code, F.n(11, 99), None), ("adjust", code, -F.n(11, 99), None), ("adjust", code, qs[2], F.cost()), ("in", code, qs[3], F.cost())]
    hs = led_lines(mv)
    add(_c("Ledger.history", "ledger", f"led.history('{code}')", view=HIST_VIEW, setup=hs, tags=("default",)))
    add(_c("Ledger.history", "ledger", f"led.history('{code.lower().replace('-', ' ')}')", view=HIST_VIEW, setup=hs, tags=("default",)))
    for since in (3, 6):
        add(_c("Ledger.history", "ledger", f"led.history('{code}', {since})", view=HIST_VIEW, setup=hs, tags=("boundary",)))
    add(_c("Ledger.history", "ledger", f"led.history('{code}', since_seq=4)", view=HIST_VIEW, setup=hs))
    add(_c("Ledger.history", "ledger", f"led.history('{code}', 0)", view=HIST_VIEW, setup=hs, tags=("boundary",)))
    add(_c("Ledger.history", "ledger", f"led.history('{F.code()}')", view=HIST_VIEW, setup=hs, tags=("empty",)))
    code, setup_l, first, total, q1 = layers_world()
    add(_c("Ledger.layers", "ledger", f"led.layers('{code}')", view=LAYERS_VIEW, setup=setup_l))
    add(_c("Ledger.layers", "ledger", f"led.layers('{code.lower().replace('-', ' ')}')", view=LAYERS_VIEW, setup=setup_l))
    code = F.code()
    qa = F.n(21, 400)
    add(_c("Ledger.layers", "ledger", f"led.layers('{code}')", view=LAYERS_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa, None)]), tags=("boundary",)))
    add(_c("Ledger.layers", "ledger", f"led.layers('{code}')", view=LAYERS_VIEW, setup=led_lines([("in", code, qa, None), ("adjust", code, -F.n(3, 19), None), ("adjust", code, F.n(3, 19), F.cost())])))
    add(_c("Ledger.layers", "ledger", f"led.layers('{F.code()}')", view=LAYERS_VIEW, setup=led_lines([("in", code, qa, F.cost())]), tags=("empty",)))
    # --- Reservations ---
    def rworld() -> tuple[str, str, int, list[str]]:
        code, other = F.code(), F.code()
        oh = F.n(200, 900)
        h1, h2, h3 = F.n(11, 90), F.n(11, 90), F.n(11, 90)
        s = led_lines([("in", code, oh, F.cost()), ("in", other, F.n(200, 900), F.cost()), ("out", code, F.n(11, 90), None)])
        s += res_lines([(code, h1, F.oid()), (other, h2, F.oid()), (code, h3, F.oid())])
        return code, other, h1 + h3, s

    code, other, held, rs = rworld()
    add(_c("Reservations.reserved", "reservations", f"res.reserved('{code}')", setup=rs))
    add(_c("Reservations.reserved", "reservations", f"res.reserved('{other.lower()}')", setup=rs))
    add(_c("Reservations.reserved", "reservations", f"res.reserved('{F.code()}')", setup=rs, tags=("empty",)))
    add(_c("Reservations.reserved", "reservations", f"res.reserved('{code}')", setup=led_lines([("in", code, F.n(200, 900), None)]) + res_lines([]), tags=("empty",)))
    oid = F.oid()
    add(_c("Reservations.reserved", "reservations", f"res.reserved('{code}')", setup=led_lines([("in", code, F.n(200, 900), None)]) + res_lines([(code, F.n(11, 90), oid), (code, F.n(11, 90), oid)])))
    for _ in range(2):
        code, other, held, rs = rworld()
        add(_c("Reservations.available", "reservations", f"res.available('{code.lower().replace('-', ' ')}')", setup=rs))
    code, other, held, rs = rworld()
    add(_c("Reservations.available", "reservations", f"res.available('{other}')", setup=rs))
    add(_c("Reservations.available", "reservations", f"res.available('{F.code()}')", setup=rs, tags=("empty",)))
    q = F.n(31, 900)
    code = F.code()
    add(_c("Reservations.available", "reservations", f"res.available('{code}')", setup=led_lines([("in", code, q, F.cost())]) + res_lines([(code, q, F.oid())]), tags=("boundary",)))
    add(_c("Reservations.available", "reservations", f"res.available('{code}')", setup=led_lines([("in", code, q, F.cost())]) + ["res = Reservations(led)"], tags=("default",)))
    add(_c("Reservations.available", "reservations", f"res.available('{code}')", setup=led_lines([("in", code, q, F.cost()), ("adjust", code, -F.n(11, 29), None)]) + res_lines([(code, F.n(30, q - 40), F.oid())])))
    oh = F.n(200, 900)
    code = F.code()
    h = F.n(11, 90)
    one_h = led_lines([("in", code, oh, F.cost())]) + res_lines([(code, h, F.oid())])
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {oh - h}, '{F.oid()}')", setup=one_h, tags=("boundary",)))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {oh - h + 1}, '{F.oid()}')", raises="ValueError", setup=one_h, tags=("boundary",)))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code.lower()}', {F.n(11, oh - h - 1)}, '{F.oid()}')", setup=one_h))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', 0, '{F.oid()}')", raises="ValueError", setup=one_h, tags=("boundary",)))
    for _ in range(3):
        oh3, h3 = F.n(200, 900), F.n(11, 90)
        c3 = F.code()
        add(_c("Reservations.reserve", "reservations", f"res.reserve('{c3}', {oh3 - h3}, '{F.oid()}')", setup=led_lines([("in", c3, oh3, F.cost())]) + res_lines([(c3, h3, F.oid())]), tags=("boundary",)))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', -{F.n(11, 99)}, '{F.oid()}')", raises="ValueError", setup=one_h))
    oid = F.oid()
    ohd = led_lines([("in", code, oh, F.cost())]) + res_lines([(code, h, oid)])
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {F.n(11, 90)}, '{oid}')", setup=ohd))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{F.code()}', {F.n(11, 90)}, '{F.oid()}')", raises="ValueError", setup=ohd))
    iss = F.n(11, 90)
    s2 = led_lines([("in", code, oh, F.cost()), ("out", code, iss, None)]) + res_lines([(code, h, F.oid())])
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {oh - iss - h}, '{F.oid()}')", setup=s2, tags=("boundary",)))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {oh - iss - h + 1}, '{F.oid()}')", raises="ValueError", setup=s2, tags=("boundary",)))
    add(_c("Reservations.reserve", "reservations", f"res.reserve('{code}', {F.n(11, 90)}, '{F.oid()}')", setup=led_lines([("in", code, oh, F.cost())]) + ["res = Reservations(led)"], tags=("default",)))
    code, other, held, rs = rworld()
    oid = F.oid()
    rs2 = rs[:-1] + [f"res.reserve('{code}', {F.n(11, 90)}, '{oid}')", f"res.reserve('{other}', {F.n(11, 90)}, '{oid}')"]
    add(_c("Reservations.release", "reservations", f"res.release('{oid}')", setup=rs2))
    add(_c("Reservations.release", "reservations", f"res.release('{F.oid()}')", setup=rs, tags=("empty",)))
    add(_c("Reservations.release", "reservations", f"res.release('{oid}')", setup=led_lines([("in", code, F.n(200, 900), None)]) + ["res = Reservations(led)"], tags=("empty",)))
    add(_c("Reservations.release", "reservations", f"res.release('{oid}')", setup=rs + [f"res.reserve('{code}', {F.n(11, 29)}, '{oid}')"]))
    # --- costing ---
    code, s, first, total, q1 = layers_world()
    for qq in (first - 1, first, first + 1, first + F.n(3, 9), total - 1, total):
        add(_c("cogs", "costing", f"cogs(led, '{code}', {qq})", view=DEC_VIEW, setup=s, tags=("boundary",)))
    add(_c("cogs", "costing", f"cogs(led, '{code}', {total + 1})", raises="ValueError", setup=s, tags=("boundary",)))
    add(_c("cogs", "costing", f"cogs(led, '{code}', 0)", raises="ValueError", setup=s, tags=("boundary",)))
    code = F.code()
    qa, qb, ia = F.n(21, 400), F.n(21, 400), F.n(11, 20)
    add(_c("cogs", "costing", f"cogs(led, '{code.lower()}', {F.n(3, 19)})", view=DEC_VIEW, setup=led_lines([("in", code, qa, None), ("in", code, qb, F.cost()), ("out", code, qa, None)])))
    add(_c("cogs", "costing", f"cogs(led, '{code}', {F.n(21, qa + qb - 30)})", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("adjust", code, -ia, None), ("adjust", code, qb, F.cost())])))
    for _ in range(3):
        code, s, first, total, q1 = layers_world()
        add(_c("inventory_value", "costing", f"inventory_value(led, '{code.lower().replace('-', ' ')}')", view=DEC_VIEW, setup=s))
    code = F.code()
    qa = F.n(21, 400)
    add(_c("inventory_value", "costing", f"inventory_value(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa, None)]), tags=("boundary",)))
    add(_c("inventory_value", "costing", f"inventory_value(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, None), ("in", code, F.n(3, 9), F.cost())])))
    add(_c("inventory_value", "costing", f"inventory_value(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("adjust", code, -F.n(11, 20), None), ("adjust", code, F.n(3, 9), F.cost())])))
    add(_c("inventory_value", "costing", f"inventory_value(led, '{F.code()}')", view=DEC_VIEW, setup=["led = Ledger()"], tags=("empty",)))
    for _ in range(5):
        code = F.code()
        a = F.cost(even=True)
        b = f"{float(a) + 0.01:.2f}"
        add(_c("average_cost", "costing", f"average_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, 1, a), ("in", code, 1, b)]), tags=("tie",)))
    code, s, first, total, q1 = layers_world()
    add(_c("average_cost", "costing", f"average_cost(led, '{code.lower()}')", view=DEC_VIEW, setup=s))
    add(_c("average_cost", "costing", f"average_cost(led, '{F.code()}')", view=DEC_VIEW, setup=["led = Ledger()"], tags=("empty",)))
    code = F.code()
    qa = F.n(21, 400)
    add(_c("average_cost", "costing", f"average_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa, None)]), tags=("boundary",)))
    add(_c("average_cost", "costing", f"average_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa + F.n(3, 9), None)], neg=True)))
    code, s, first, total, q1 = layers_world()
    add(_c("next_cost", "costing", f"next_cost(led, '{code}')", view=DEC_VIEW, setup=s))
    code = F.code()
    qa = F.n(21, 400)
    add(_c("next_cost", "costing", f"next_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("in", code, F.n(21, 400), F.cost()), ("out", code, qa, None)]), tags=("boundary",)))
    add(_c("next_cost", "costing", f"next_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa - 1, None)])))
    add(_c("next_cost", "costing", f"next_cost(led, '{F.code()}')", view=DEC_VIEW, setup=["led = Ledger()"], tags=("empty",)))
    add(_c("next_cost", "costing", f"next_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, F.cost()), ("out", code, qa, None)]), tags=("boundary",)))
    add(_c("next_cost", "costing", f"next_cost(led, '{code}')", view=DEC_VIEW, setup=led_lines([("in", code, qa, None), ("in", code, F.n(3, 9), F.cost())])))
    # --- replenish ---
    def fac() -> str:
        return f"{F.n(111, 399) / 100:.2f}"

    for _ in range(3):  # 既定値（factor=1.5, pack_size=1）に頼る
        d, lead = F.n(3, 500), F.n(3, 365)
        add(_c("safety_stock", "replenish", f"safety_stock({d}, {lead})", tags=("default",)))
    d, lead = F.n(3, 500), F.n(3, 365)
    add(_c("safety_stock", "replenish", f"safety_stock({d}, {lead}, {fac()})"))
    add(_c("safety_stock", "replenish", f"safety_stock({d}, {lead}, pack_size={F.n(3, 400)})"))
    add(_c("safety_stock", "replenish", f"safety_stock({d}, {lead}, {fac()}, {F.n(3, 400)})"))
    add(_c("safety_stock", "replenish", f"safety_stock(Decimal('{F.n(11, 99) / 10}'), {F.n(3, 365)})"))
    add(_c("safety_stock", "replenish", f"safety_stock(Decimal('{F.n(11, 99) / 10}'), {F.n(3, 365)}, {fac()}, {F.n(3, 400)})"))
    add(_c("safety_stock", "replenish", f"safety_stock({F.n(3, 500)}, 0)", tags=("boundary",)))
    add(_c("safety_stock", "replenish", f"safety_stock(0, {F.n(3, 365)})", tags=("boundary",)))
    ev = F.n(3, 9) * 4
    add(_c("safety_stock", "replenish", f"safety_stock({ev}, {F.n(3, 365)}, 1.5)", tags=("boundary",)))
    add(_c("safety_stock", "replenish", f"safety_stock(-{F.n(3, 500)}, {F.n(3, 365)})", raises="ValueError", tags=("boundary",)))
    add(_c("safety_stock", "replenish", f"safety_stock({F.n(3, 500)}, -{F.n(3, 365)})", raises="ValueError"))

    def rcase(point: int, onhand: int, held: int, lead: int = 0, pack: int = 1) -> tuple[str, list[str]]:
        code = F.code()
        s = cat_lines([(code, point, lead or F.n(3, 365), pack)])
        s += led_lines([("in", code, onhand, F.cost())]) if onhand else ["led = Ledger()"]
        s += res_lines([(code, held, F.oid())] if held else [])
        return code, s

    for _ in range(2):  # 発注点ちょうど・1 つ上・1 つ下
        p = F.n(31, 3000)
        for delta in (0, 1, -1):
            code, s = rcase(p, p + delta, 0)
            add(_c("needs_reorder", "replenish", f"needs_reorder(cat, res, '{code}')", setup=s, tags=("boundary",)))
    p, h = F.n(31, 3000), F.n(11, 90)
    code, s = rcase(p, p + h, h)
    add(_c("needs_reorder", "replenish", f"needs_reorder(cat, res, '{code.lower()}')", setup=s, tags=("boundary",)))
    code, s = rcase(p, p + h + 1, h)
    add(_c("needs_reorder", "replenish", f"needs_reorder(cat, res, '{code}')", setup=s, tags=("boundary",)))
    add(_c("needs_reorder", "replenish", f"needs_reorder(cat, res, '{F.code()}')", raises="KeyError", setup=s))
    for _ in range(5):
        onhand, held, p = F.n(30, 5000), F.n(11, 90), F.n(11, 3000)
        code, s = rcase(p, onhand, held, pack=F.n(3, 400))
        add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code}', {F.n(3, 365)})", setup=s))
    for _ in range(5):
        code, s = rcase(F.n(11, 3000), F.n(30, 5000), F.n(11, 90), pack=F.n(3, 400))
        add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code.lower()}', Decimal('{F.n(11, 99) / 10}'))", setup=s))
    code, s = rcase(F.n(11, 3000), F.n(30, 5000), 0, pack=F.n(3, 400))
    add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code}', 0)", setup=s, tags=("empty",)))
    code, s = rcase(F.n(11, 3000), F.n(3000, 9000), 0, pack=F.n(3, 400))
    add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code}', {F.n(3, 365)})", setup=s, tags=("boundary",)))
    code, s = rcase(F.n(11, 3000), 0, 0, pack=F.n(3, 400))
    add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code}', {F.n(3, 365)})", setup=s))
    for _ in range(6):
        code, s = rcase(F.n(11, 3000), F.n(30, 5000), F.n(11, 90), pack=F.n(3, 400))
        add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{code}', None)", setup=s, tags=("none",)))
    add(_c("reorder_qty", "replenish", f"reorder_qty(cat, res, '{F.code()}', {F.n(3, 365)})", raises="KeyError", setup=s))
    # --- reports ---
    def multi(n: int, layered: bool = False) -> tuple[list[tuple[str, int, int, int]], list[str]]:
        skus = [sku4() for _ in range(n)]
        mv: list[Move] = [("in", c, F.n(500 if layered else 200, 900), F.cost()) for c, *_ in skus]
        if layered:
            for c, *_ in skus:
                mv += [("in", c, F.n(21, 400), F.cost()), ("out", c, F.n(400, 440), None)]
        s = cat_lines(skus) + led_lines(mv)
        return skus, s

    for held_kind in ("none", "some", "all", "some"):
        skus, s = multi(5)
        if held_kind == "none":
            holds: list[tuple[str, int, str]] = []
        else:
            holds = []
            for c, point, _, _ in skus:
                if held_kind == "all" or rng.random() < 0.6:
                    holds.append((c, F.n(100, 190), F.oid()))
        add(_c("low_stock", "reports", "low_stock(cat, res)", setup=s + res_lines(holds)))
    # 不足数が同数 → コード順、発注点ちょうど、発注点の 1 つ上
    a, b, c3 = F.word(2), F.word(2), F.word(2)
    pt = F.n(31, 3000)
    sk = [(f"{b}-{F.n(1000, 9999)}", pt, 5, 1), (f"{a}-{F.n(1000, 9999)}", pt, 5, 1), (f"{c3}-{F.n(1000, 9999)}", pt, 5, 1)]
    add(_c("low_stock", "reports", "low_stock(cat, res)", setup=cat_lines(sk) + led_lines([("in", sk[0][0], pt - F.n(11, 29), None), ("in", sk[1][0], pt - F.n(11, 29), None), ("in", sk[2][0], pt + F.n(11, 29), None)]) + res_lines([]), tags=("tie",)))
    sk2 = [(f"{F.word(2)}-{F.n(1000, 9999)}", pt + i * 7, 5, 1) for i in range(3)]
    add(_c("low_stock", "reports", "low_stock(cat, res)", setup=cat_lines(sk2) + led_lines([("in", sk2[0][0], pt, None), ("in", sk2[1][0], pt + 8, None), ("in", sk2[2][0], pt + 13, None)]) + res_lines([]), tags=("boundary",)))
    add(_c("low_stock", "reports", "low_stock(cat, res)", setup=["cat = Catalog()", "led = Ledger()", "res = Reservations(led)"], tags=("empty",)))
    sk3 = [(F.code(), F.n(100, 3000), 5, 1)]
    add(_c("low_stock", "reports", "low_stock(cat, res)", setup=cat_lines(sk3) + ["led = Ledger()", "res = Reservations(led)"], tags=("default",)))
    # 編集タスク用: 手持ちは発注点より多いが、引当を差し引くと不足になる
    code = F.code()
    pt = F.n(31, 3000)
    heldq = F.n(200, 900)
    add(_c("low_stock", "reports", "low_stock(cat, res)", setup=cat_lines([(code, pt, 5, 12)]) + led_lines([("in", code, pt + heldq - F.n(11, 29), None)]) + res_lines([(code, heldq, F.oid())]), tags=("edge_task",)))
    for lay in (False, True, True, True, True, True):
        skus, s = multi(3, lay)
        add(_c("valuation", "reports", "valuation(cat, led)", view=DEC_VIEW, setup=s))
    code, code2 = F.code(), F.code()
    a1 = F.cost(even=True)
    sk5 = [(code, 20, 5, 1), (code2, 20, 5, 1)]
    for _ in range(4):  # 合計が x.xx5 になる（偶数丸めと 0 から遠い方への丸めが分かれる）
        c1, c2 = F.code(), F.code()
        add(_c("valuation", "reports", "valuation(cat, led)", view=DEC_VIEW, setup=cat_lines([(c1, 20, 5, 1), (c2, 20, 5, 1)]) + led_lines([("in", c1, 1, F.cost(even=True) + "5"), ("in", c2, F.n(21, 400), F.cost())]), tags=("tie",)))
    add(_c("valuation", "reports", "valuation(cat, led)", view=DEC_VIEW, setup=["cat = Catalog()"] + led_lines([("in", code, F.n(21, 400), F.cost())]), tags=("empty",)))
    add(_c("valuation", "reports", "valuation(cat, led)", view=DEC_VIEW, setup=cat_lines([(code, 20, 5, 1)]) + led_lines([("in", code, F.n(21, 400), F.cost()), ("in", code2, F.n(21, 400), F.cost())])))
    def rp_world() -> tuple[list[tuple[str, int, int, int]], list[str], str]:
        skus, s = multi(4, True)
        dem = {c: F.n(3, 365) for c, *_ in skus}
        dtxt = "{" + ", ".join(f"'{k}': {v}" for k, v in dem.items()) + "}"
        holds = [(c, F.n(11, 60), F.oid()) for c, *_ in skus[:2]]
        return skus, s + res_lines(holds), dtxt

    skus, rp, dtxt = rp_world()
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {dtxt})", setup=rp))
    skus, rp, dtxt = rp_world()
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {dtxt}, {F.n(11, 200)})", setup=rp, tags=("boundary",)))
    skus, rp, dtxt = rp_world()
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {dtxt}, min_qty={F.n(201, 400)})", setup=rp, tags=("boundary",)))
    add(_c("restock_plan", "reports", "restock_plan(cat, res, {})", setup=rp, tags=("empty",)))
    k0 = skus[0][0]
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {{'{k0}': Decimal('{F.n(11, 99) / 10}')}})", setup=rp))
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {{'{k0}': {F.n(3, 365)}}}, 0)", setup=rp, tags=("boundary",)))
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {{'{F.code()}': {F.n(3, 365)}}})", setup=rp))
    add(_c("restock_plan", "reports", f"restock_plan(cat, res, {{'{k0}': {F.n(3, 365)}}})", setup=["cat = Catalog()", "led = Ledger()", "res = Reservations(led)"], tags=("empty",)))
    for _ in range(3):
        skus, rp, dtxt = rp_world()
        add(_c("restock_plan", "reports", f"restock_plan(cat, res, {dtxt})", setup=rp))
    return cs
