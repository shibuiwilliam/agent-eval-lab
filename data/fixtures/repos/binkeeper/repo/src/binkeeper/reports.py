"""レポート（不足一覧・在庫評価・補充計画）。"""

from __future__ import annotations

from decimal import Decimal

from binkeeper.catalog import Catalog
from binkeeper.costing import inventory_value, round_cost
from binkeeper.ledger import Ledger
from binkeeper.replenish import needs_reorder, reorder_qty
from binkeeper.reservations import Reservations


def _shortage(point: int, available: int) -> int:
    """発注点に対する不足数。"""
    return point - available


def low_stock(catalog: Catalog, reservations: Reservations) -> list[tuple[str, int]]:
    """発注が必要な SKU の (コード, 不足数) を、不足数の降順・同数ならコード順で返す。

    不足数 = 発注点 - 引当可能数（引当済みの数は在庫として数えない）。ちょうど発注点なら不足数 0 で載る。
    """
    rows: list[tuple[str, int]] = []
    for sku in catalog.find(""):
        if needs_reorder(catalog, reservations, sku.code):
            rows.append((sku.code, _shortage(sku.reorder_point, reservations.available(sku.code))))
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def valuation(catalog: Catalog, ledger: Ledger) -> Decimal:
    """登録済みの全 SKU の在庫評価額の合計を小数 2 桁に丸めたもの（同点は 0 から遠い方）。"""
    total = Decimal(0)
    for sku in catalog.find(""):
        total += inventory_value(ledger, sku.code)
    return round_cost(total)


def restock_plan(
    catalog: Catalog,
    reservations: Reservations,
    demand: dict[str, Decimal | int],
    min_qty: int = 1,
) -> list[tuple[str, int]]:
    """補充計画。demand は コード -> 日次需要（載っていない SKU は 0）。

    各 SKU の発注数量（reorder_qty）が min_qty 以上のものを (コード, 数量) でコード順に返す。
    """
    plan: list[tuple[str, int]] = []
    for sku in catalog.find(""):
        qty = reorder_qty(catalog, reservations, sku.code, demand.get(sku.code, 0))
        if qty >= min_qty:
            plan.append((sku.code, qty))
    return plan
