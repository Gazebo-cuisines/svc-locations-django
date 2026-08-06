"""FIFO ordering for on-hand batches. Oldest use_by first, undated last."""

from __future__ import annotations

from django.db.models import F

from stock_ledger.models import StockBalance
from stock_ledger.util.serialize import BALANCE_SELECT_RELATED


FIFO_ORDER = (
    F('lot__use_by').asc(nulls_last=True),
    F('lot__production_date').asc(nulls_last=True),
    'lot_id',
    'location_id',
)


def fifo_balances(*, product_id: int, location_id: int | None = None):
    qs = (
        StockBalance.objects
        .filter(lot__product_id=product_id, quantity__gt=0)
        .select_related(*BALANCE_SELECT_RELATED)
    )
    if location_id is not None:
        qs = qs.filter(location_id=location_id)
    return qs.order_by(*FIFO_ORDER)
