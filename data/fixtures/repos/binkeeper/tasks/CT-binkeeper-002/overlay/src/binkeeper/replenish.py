"""補充（安全在庫・発注判定・発注数量）。"""

from __future__ import annotations

import math
from decimal import Decimal

from binkeeper.catalog import Catalog, round_up_to
from binkeeper.reservations import Reservations


def safety_stock(
    daily_demand: Decimal | int,
    lead_time_days: int,
    factor: float = 1.5,
    pack_size: int = 1,
) -> int:
    """安全在庫 = 日次需要 x リードタイム(日) x factor を切り上げ、pack_size の倍数にしたもの。

    daily_demand と lead_time_days が負なら ValueError。
    """
    if daily_demand < 0 or lead_time_days < 0:
        raise ValueError("需要とリードタイムは 0 以上")
    raw = Decimal(daily_demand) * lead_time_days * Decimal(str(factor))
    return round_up_to(math.ceil(raw), pack_size)


def needs_reorder(catalog: Catalog, reservations: Reservations, sku: str) -> bool:
    """引当可能数が発注点以下なら True（ちょうど発注点でも True）。未登録の SKU は KeyError。"""
    return reservations.available(sku) <= catalog.get(sku).reorder_point


def reorder_qty(
    catalog: Catalog, reservations: Reservations, sku: str, daily_demand: Decimal | int | None
) -> int:
    """発注数量。日次需要が None なら 0 として扱う。

    目標在庫 = 日次需要 x リードタイム を切り上げたもの + 安全在庫（factor 1.5）。
    目標から引当可能数を引いた不足を pack_size の倍数に切り上げる。不足が無ければ 0。
    """
    item = catalog.get(sku)
    demand = daily_demand
    cover = math.ceil(Decimal(demand) * item.lead_time_days)
    target = cover + safety_stock(demand, item.lead_time_days)
    shortfall = target - reservations.available(sku)
    return round_up_to(max(shortfall, 0), item.pack_size)
