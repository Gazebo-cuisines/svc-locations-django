"""Resolve product_supplier mapping for a lot or stock entry."""

from __future__ import annotations

from product.models import ProductSupplier
from stock_ledger.models import StockEntry, StockEntryType


def product_supplier_for_lot(lot):
    if lot is None:
        return None
    if lot.product_supplier_id:
        cached = getattr(lot, 'product_supplier', None)
        if cached is not None:
            return cached
        return (
            ProductSupplier.objects
            .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
            .filter(pk=lot.product_supplier_id)
            .first()
        )
    return (
        ProductSupplier.objects
        .filter(product_id=lot.product_id, is_active=True)
        .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
        .order_by('-is_default', '-id')
        .first()
    )


def product_supplier_for_entry(entry: StockEntry):
    """Shape mapping stamped on the lot, else best-effort for receipts."""
    lot = entry.lot
    hit = product_supplier_for_lot(lot)
    if hit is not None:
        return hit
    if entry.entry_type != StockEntryType.RECEIPT:
        return None
    if lot is None or entry.counterparty_location_id is None:
        return None
    qs = (
        ProductSupplier.objects
        .filter(
            product_id=lot.product_id,
            supplier_id=entry.counterparty_location_id,
            is_active=True,
        )
        .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
    )
    if lot.shape_format_id:
        shaped = qs.filter(purchase_shape_format_id=lot.shape_format_id).first()
        if shaped is not None:
            return shaped
    return qs.filter(is_default=True).first() or qs.order_by('-id').first()
