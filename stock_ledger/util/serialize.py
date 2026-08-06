from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from stock_ledger.models import StockBalance, StockEntry, StockEntryType

BALANCE_SELECT_RELATED = (
    'location',
    'lot__product__product_class',
    'lot__product__range',
    'lot__product__unit',
    'lot__product__yield_data',
)


def _dec(value):
    return str(value) if value is not None else None


def receipt_meta_by_lot_ids(lot_ids: set[int]) -> dict[int, dict]:
    """lot_id → supplier + po from purchase receipt (prefer one that has them)."""
    out: dict[int, dict] = {}
    if not lot_ids:
        return out

    for entry in (
        StockEntry.objects
        .filter(
            lot_id__in=lot_ids,
            entry_type=StockEntryType.RECEIPT,
        )
        .select_related('counterparty_location')
        .order_by('id')
    ):
        loc = entry.counterparty_location
        candidate = {
            'supplier_id': loc.id if loc is not None else None,
            'supplier_name': loc.name if loc is not None else None,
            'po_number': entry.po_number or None,
        }
        existing = out.get(entry.lot_id)
        if existing is None:
            out[entry.lot_id] = candidate
            continue
        # Prefer a later/earlier receipt that actually has supplier or PO.
        if existing.get('supplier_id') is None and candidate['supplier_id'] is not None:
            existing['supplier_id'] = candidate['supplier_id']
            existing['supplier_name'] = candidate['supplier_name']
        if existing.get('po_number') is None and candidate['po_number'] is not None:
            existing['po_number'] = candidate['po_number']
    return out


def serialize_balance_row(
    balance: StockBalance,
    *,
    receipt_meta: dict | None = None,
) -> dict:
    product = balance.lot.product
    try:
        yield_factor = _dec(product.yield_data.yield_factor)
    except ObjectDoesNotExist:
        yield_factor = None
    meta = receipt_meta or {}
    return {
        'lot_id': balance.lot_id,
        'product_id': balance.lot.product_id,
        'product_name': product.name if balance.lot.product_id else None,
        'recipe_code': product.recipe_code if balance.lot.product_id else None,
        'product_class_id': product.product_class_id,
        'product_class_name': (
            product.product_class.name if product.product_class_id else None
        ),
        'range_id': product.range_id,
        'range_name': product.range.name if product.range_id else None,
        'unit_id': product.unit_id,
        'unit_name': product.unit.name if product.unit_id else None,
        'yield_factor': yield_factor,
        'trace_number': balance.lot.trace_number,
        'production_date': (
            balance.lot.production_date.isoformat()
            if balance.lot.production_date
            else None
        ),
        'use_by': balance.lot.use_by.isoformat() if balance.lot.use_by else None,
        'location_id': balance.location_id,
        'location_name': balance.location.name if balance.location_id else None,
        'supplier_id': meta.get('supplier_id'),
        'supplier_name': meta.get('supplier_name'),
        'po_number': meta.get('po_number'),
        'quantity': _dec(balance.quantity),
        'quantity_base': _dec(balance.quantity_base),
        'last_entry_id': balance.last_entry_id,
        'updated_at': balance.updated_at.isoformat() if balance.updated_at else None,
    }


def load_balance_for_row(*, lot_id: int, location_id: int) -> StockBalance | None:
    return (
        StockBalance.objects.select_related(*BALANCE_SELECT_RELATED)
        .filter(lot_id=lot_id, location_id=location_id)
        .first()
    )
