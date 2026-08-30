"""Stock movement + closing-stock report queries."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
)

_POSTED_EXCLUDE = [
    StockEntryPostingStatus.QUEUED,
    StockEntryPostingStatus.CANCELLED,
]

_MOVEMENT_SELECT = (
    'unit',
    'location',
    'counterparty_location',
    'lot__product',
)

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _as_of_end(as_of: date):
    """Inclusive end of calendar day in the active timezone."""
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(as_of, time(23, 59, 59)), tz)


def _posted_entries():
    return StockEntry.objects.exclude(posting__status__in=_POSTED_EXCLUDE)


def _apply_product_filters(qs, *, product_id=None, goods_in_type=None, location_id=None):
    if product_id is not None:
        qs = qs.filter(lot__product_id=product_id)
    if goods_in_type not in (None, ''):
        qs = qs.filter(lot__product__goods_in_type=goods_in_type)
    if location_id is not None:
        qs = qs.filter(location_id=location_id)
    return qs


def movement_row(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product if lot is not None else None
    location = entry.location
    unit = entry.unit
    return {
        'entry_id': entry.id,
        'entry_type': entry.entry_type,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'product_id': product.id if product is not None else None,
        'product_name': product.name if product is not None else None,
        'recipe_code': product.recipe_code if product is not None else None,
        'gff_code': product.gff_code if product is not None else None,
        'goods_in_type': product.goods_in_type if product is not None else None,
        'lot_id': entry.lot_id,
        'trace_number': lot.trace_number if lot is not None else None,
        'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
        'location_id': entry.location_id,
        'location_name': location.name if location is not None else None,
        'quantity': _dec(entry.quantity),
        'quantity_base': _dec(entry.quantity_base),
        'unit_id': entry.unit_id,
        'unit_name': unit.name if unit is not None else None,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'po_number': entry.po_number,
        'remarks': entry.remarks,
    }


def movements_report(
    *,
    entry_type: str,
    date_from: date,
    date_to: date,
    product_id: int | None = None,
    goods_in_type: str | None = None,
    location_id: int | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """Posted receipt/issue rows by effective_at date range (inclusive)."""
    row_limit = max(1, min(int(limit), _MAX_LIMIT))
    qs = (
        _posted_entries()
        .filter(
            entry_type=entry_type,
            effective_at__date__gte=date_from,
            effective_at__date__lte=date_to,
        )
        .select_related(*_MOVEMENT_SELECT)
        .order_by('effective_at', 'id')
    )
    qs = _apply_product_filters(
        qs,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
    )
    return [movement_row(entry) for entry in qs[:row_limit]]


def closing_balances_as_of(
    *,
    as_of: date,
    product_id: int | None = None,
    goods_in_type: str | None = None,
    location_id: int | None = None,
    include_zero: bool = False,
) -> list[dict]:
    """
    Lot×location closing qty: SUM(quantity) where effective_at <= end of as_of day.
    Excludes queued/cancelled postings and downtime (qty 0, non-stock).
    """
    cutoff = _as_of_end(as_of)
    qs = (
        _posted_entries()
        .filter(effective_at__lte=cutoff)
        .exclude(entry_type=StockEntryType.DOWNTIME)
        .filter(lot__product__is_active=True)
    )
    qs = _apply_product_filters(
        qs,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
    )

    aggregated = list(
        qs.values(
            'lot_id',
            'location_id',
            'lot__product_id',
            'lot__product__name',
            'lot__product__recipe_code',
            'lot__product__gff_code',
            'lot__product__goods_in_type',
            'lot__product__unit_id',
            'lot__product__unit__name',
            'lot__trace_number',
            'lot__use_by',
            'location__name',
        )
        .annotate(
            quantity=Sum('quantity'),
            quantity_base=Sum('quantity_base'),
        )
        .order_by('lot__product__name', 'lot_id', 'location_id')
    )

    rows: list[dict] = []
    for row in aggregated:
        qty = row['quantity'] or Decimal('0')
        if not include_zero and qty == 0:
            continue
        use_by = row['lot__use_by']
        rows.append({
            'as_of': as_of.isoformat(),
            'product_id': row['lot__product_id'],
            'product_name': row['lot__product__name'],
            'recipe_code': row['lot__product__recipe_code'],
            'gff_code': row['lot__product__gff_code'],
            'goods_in_type': row['lot__product__goods_in_type'],
            'lot_id': row['lot_id'],
            'trace_number': row['lot__trace_number'],
            'use_by': use_by.isoformat() if use_by else None,
            'location_id': row['location_id'],
            'location_name': row['location__name'],
            'unit_id': row['lot__product__unit_id'],
            'unit_name': row['lot__product__unit__name'],
            'quantity': _dec(qty),
            'quantity_base': _dec(row['quantity_base']),
        })
    return rows
