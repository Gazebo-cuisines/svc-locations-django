from datetime import date
from decimal import Decimal

from purchasing.models import PurchaseOrderLine, PurchaseOrderStatus
from stock_ledger.util.conversions import StockValidationError, packs_to_stock

OPEN_PO_STATUSES = (
    PurchaseOrderStatus.ORDERED,
    PurchaseOrderStatus.PARTIAL,
)


def _pack_unit_name(line) -> str | None:
    ps = line.product_supplier
    if ps is not None and ps.outer_unit_id:
        return ps.outer_unit.name
    return line.unit.name if line.unit_id else None


def _remaining_stock(line, remaining: Decimal) -> Decimal:
    ps = line.product_supplier
    product = line.product
    if ps is not None and product is not None:
        try:
            return packs_to_stock(remaining, ps, product)
        except StockValidationError:
            pass
    return remaining


def open_pos_for_location(slots: list[dict] | None, location_id: int | None) -> list[dict]:
    return [
        s for s in (slots or [])
        if s.get('ship_to_location_id') in (None, location_id)
    ]


def open_po_slots_for_products(product_ids: set[int]) -> dict[int, list[dict]]:
    """Remaining Ordered/Partial PO qty per product, earliest expected_at first."""
    if not product_ids:
        return {}
    lines = (
        PurchaseOrderLine.objects.filter(
            product_id__in=product_ids,
            qty_balance__gt=0,
            purchase_order__status__in=OPEN_PO_STATUSES,
        ).select_related(
            'purchase_order',
            'unit',
            'product',
            'product_supplier__outer_unit',
        )
    )
    combined: dict[tuple[int, int], dict] = {}
    for line in lines:
        po = line.purchase_order
        key = (line.product_id, po.id)
        slot = combined.get(key)
        remaining = line.qty_balance
        stock_remaining = _remaining_stock(line, remaining)
        if slot is None:
            combined[key] = {
                'product_id': line.product_id,
                'qty_remaining': remaining,
                'qty_remaining_stock': stock_remaining,
                'delivery_date': (
                    po.expected_at.isoformat() if po.expected_at else None
                ),
                'po_number': po.external_number or po.number,
                'po_id': po.id,
                'unit_name': _pack_unit_name(line),
                'ship_to_location_id': po.ship_to_location_id,
                'sort_date': po.expected_at,
            }
        else:
            slot['qty_remaining'] += remaining
            slot['qty_remaining_stock'] += stock_remaining

    by_product: dict[int, list[dict]] = {}
    for slot in combined.values():
        by_product.setdefault(slot['product_id'], []).append(slot)
    out: dict[int, list[dict]] = {}
    for product_id, slots in by_product.items():
        slots.sort(
            key=lambda s: (
                s['sort_date'] is None,
                s['sort_date'] or date.min,
                s['po_id'],
            ),
        )
        out[product_id] = [
            {
                'po_id': s['po_id'],
                'qty_remaining': str(s['qty_remaining']),
                'qty_remaining_stock': str(s['qty_remaining_stock']),
                'delivery_date': s['delivery_date'],
                'po_number': s['po_number'],
                'unit_name': s['unit_name'],
                'ship_to_location_id': s['ship_to_location_id'],
            }
            for s in slots
        ]
    return out
