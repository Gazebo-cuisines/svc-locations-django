from __future__ import annotations

from datetime import date
from decimal import Decimal

from planning.adapters.locations import location_min_shelf_life
from planning.adapters.types import LotSpec, ProductSpec


def required_min_shelf_life_days(
    product: ProductSpec,
    destination_location_id: int | None,
) -> int:
    loc_min = location_min_shelf_life(destination_location_id)
    return max(product.absolute_min_shelf_life_days, loc_min, 0)


def is_lot_eligible(
    lot: LotSpec,
    *,
    plan_date: date,
    min_shelf_life_days: int,
) -> bool:
    if lot.use_by is None:
        # No use-by: allow only when no shelf-life gate required
        return min_shelf_life_days <= 0
    remaining = (lot.use_by - plan_date).days
    return remaining >= min_shelf_life_days


def eligible_lots(
    lots: list[LotSpec],
    *,
    plan_date: date,
    product: ProductSpec,
    destination_location_id: int | None,
) -> list[LotSpec]:
    """FEFO-ordered eligible lots (use_by, production_date, lot_id)."""
    min_days = required_min_shelf_life_days(product, destination_location_id)
    filtered = [
        lot
        for lot in lots
        if is_lot_eligible(lot, plan_date=plan_date, min_shelf_life_days=min_days)
        and lot.atp > 0
    ]
    filtered.sort(
        key=lambda lot: (
            lot.use_by or date.max,
            lot.production_date or date.max,
            lot.lot_id,
        )
    )
    return filtered


def eligible_on_hand_total(
    lots: list[LotSpec],
    *,
    plan_date: date,
    product: ProductSpec,
    destination_location_id: int | None,
) -> Decimal:
    return sum(
        (lot.atp for lot in eligible_lots(
            lots,
            plan_date=plan_date,
            product=product,
            destination_location_id=destination_location_id,
        )),
        Decimal('0'),
    )
