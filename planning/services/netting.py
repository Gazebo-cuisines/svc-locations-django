from __future__ import annotations

from datetime import date
from decimal import Decimal

from planning.adapters import stock as stock_adapter
from planning.adapters.locations import plannable_locations
from planning.adapters.types import ProductSpec
from planning.services import eligibility


def available_for_netting(
    product: ProductSpec,
    *,
    plan_date: date,
) -> tuple[Decimal, Decimal]:
    """Return (eligible_atp_total, stock_on_hand_seen) for product as-of plan_date."""
    location_ids = plannable_locations(product)
    lots = stock_adapter.lots_for_product(product.id, location_ids)
    eligible = eligibility.eligible_on_hand_total(
        lots,
        plan_date=plan_date,
        product=product,
        destination_location_id=product.destination_location_id,
    )
    soft = stock_adapter.soft_allocated_quantity(product.id, location_ids)
    supply_rows = stock_adapter.projected_supply(
        product.id,
        location_ids,
        need_by=plan_date,
    )
    supply = sum((s.quantity for s in supply_rows), Decimal('0'))
    on_hand = sum((lot.quantity_on_hand for lot in lots), Decimal('0'))
    available = eligible - soft + supply
    if available < 0:
        available = Decimal('0')
    return available, on_hand


def apply_stock_netting(
    *,
    net_required: Decimal,
    gross_required: Decimal,
    process_loss: Decimal,
    product: ProductSpec,
    plan_date: date,
    consider_stock: bool,
) -> dict:
    """Return net/gross after stock, plus figures for the calc worksheet."""
    empty = {
        'net': net_required,
        'gross': gross_required,
        'on_hand': Decimal('0'),
        'available': Decimal('0'),
        'min_stock_held': Decimal('0'),
        'applied': False,
    }
    if not consider_stock:
        return empty

    available, on_hand = available_for_netting(product, plan_date=plan_date)
    min_stock_held = Decimal('0')
    if product.min_stock is not None and product.min_stock > 0:
        min_stock_held = min(product.min_stock, available)
        available = max(available - product.min_stock, Decimal('0'))

    net_after = max(net_required - available * process_loss, Decimal('0'))
    gross_after = max(gross_required - available, Decimal('0'))

    if product.max_stock is not None and product.max_stock > 0:
        # Cap production so on-hand + production does not wildly exceed max
        # (simple MVP: do not increase requirements for max_stock)
        pass

    return {
        'net': net_after,
        'gross': gross_after,
        'on_hand': on_hand,
        'available': available,
        'min_stock_held': min_stock_held,
        'applied': True,
    }
