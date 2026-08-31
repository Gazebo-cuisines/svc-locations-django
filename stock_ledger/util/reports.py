"""Stock movement + closing-stock report queries."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from product.models import Product
from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util.conversions import stock_to_kg

_POSTED_EXCLUDE = [
    StockEntryPostingStatus.QUEUED,
    StockEntryPostingStatus.CANCELLED,
]

_MOVEMENT_SELECT = (
    'unit',
    'location',
    'counterparty_location',
    'lot__product',
    'lot__product__unit',
)

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

# Warehouse goods-out posts transfer_out; plain issue stays for legacy/write-offs.
_GOODS_OUT_TYPES = (
    StockEntryType.ISSUE,
    StockEntryType.TRANSFER_OUT,
)


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


def _display_kg(qty: Decimal, product: Product | None) -> str | None:
    """Kg mass for UI — FE should not convert packs/boxes."""
    if product is None:
        return None
    return _dec(stock_to_kg(qty, product))


def movement_row(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product if lot is not None else None
    location = entry.location
    # quantity is always product.unit (Kg / Litre / …) after ledger write.
    unit = product.unit if product is not None and product.unit_id else entry.unit
    counterparty = entry.counterparty_location
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
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            counterparty.name if counterparty is not None else None
        ),
        'quantity': _dec(entry.quantity),
        'quantity_base': _dec(entry.quantity_base),
        'display_kg': _display_kg(entry.quantity, product),
        'unit_id': unit.id if unit is not None else entry.unit_id,
        'unit_name': unit.name if unit is not None else None,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'po_number': entry.po_number,
        'remarks': entry.remarks,
    }


def movements_report(
    *,
    date_from: date,
    date_to: date,
    entry_type: str | None = None,
    entry_types: list[str] | tuple[str, ...] | None = None,
    product_id: int | None = None,
    goods_in_type: str | None = None,
    location_id: int | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """Posted movement rows by effective_at date range (inclusive)."""
    types = list(entry_types) if entry_types else (
        [entry_type] if entry_type else []
    )
    if not types:
        raise ValueError('entry_type or entry_types is required')
    row_limit = max(1, min(int(limit), _MAX_LIMIT))
    qs = (
        _posted_entries()
        .filter(
            entry_type__in=types,
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


def goods_out_movements_report(**kwargs) -> list[dict]:
    """Issues + warehouse transfer_out (actual goods-out path)."""
    return movements_report(entry_types=_GOODS_OUT_TYPES, **kwargs)


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
    quantity / unit_name = product stock unit (Kg or Litre). display_kg = kg mass.
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
        # Minimal product stub — stock_to_kg only needs id + unit_id.
        product = Product(
            id=row['lot__product_id'],
            unit_id=row['lot__product__unit_id'],
        )
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
            'display_kg': _display_kg(qty, product),
        })
    return rows
