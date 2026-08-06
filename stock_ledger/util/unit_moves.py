"""Bind scanned StockUnits to a lot transfer (location / remaining)."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from stock_ledger.models import StockUnit, StockUnitStatus
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.stock_units import resolve_unit_serial

_MOVABLE = frozenset({
    StockUnitStatus.ACTIVE,
    StockUnitStatus.PARTIALLY_CONSUMED,
})


def apply_unit_moves_for_transfer(
    *,
    lot_id: int,
    from_location_id: int,
    to_location_id: int,
    quantity: Decimal,
    unit_moves: list[dict],
) -> list[StockUnit]:
    """Update physical bags after ledger transfer. Caller must be in a txn."""
    if not unit_moves:
        raise StockValidationError('unit_moves must be a non-empty list')

    parsed: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    total = Decimal('0')
    for i, row in enumerate(unit_moves):
        if not isinstance(row, dict):
            raise StockValidationError(f'unit_moves[{i}] must be an object')
        try:
            serial = resolve_unit_serial(str(row.get('unit_serial') or ''))
            qty = Decimal(str(row['quantity']))
        except (KeyError, StockValidationError) as exc:
            raise StockValidationError(
                f'unit_moves[{i}]: {exc}',
            ) from exc
        except Exception as exc:
            raise StockValidationError(
                f'unit_moves[{i}]: invalid quantity',
            ) from exc
        if qty <= 0:
            raise StockValidationError(
                f'unit_moves[{i}]: quantity must be positive',
            )
        if serial in seen:
            raise StockValidationError(
                f'duplicate unit_serial in unit_moves: {serial}',
            )
        seen.add(serial)
        parsed.append((serial, qty))
        total += qty

    if total != quantity:
        raise StockValidationError(
            f'unit_moves total {total} must equal transfer quantity {quantity}',
        )

    updated: list[StockUnit] = []
    for serial, qty in parsed:
        unit = (
            StockUnit.objects
            .select_for_update()
            .filter(unit_serial=serial)
            .first()
        )
        if unit is None:
            raise StockValidationError(f'unit_serial={serial} not found')
        if unit.lot_id != lot_id:
            raise StockValidationError(
                f'unit_serial={serial} belongs to lot {unit.lot_id}, '
                f'not transfer lot {lot_id}',
            )
        if unit.status not in _MOVABLE:
            raise StockValidationError(
                f'unit_serial={serial} status={unit.status} cannot be moved',
            )
        if unit.location_id != from_location_id:
            raise StockValidationError(
                f'unit_serial={serial} is at location {unit.location_id}, '
                f'not from_location {from_location_id}',
            )
        if qty > unit.quantity_remaining:
            raise StockValidationError(
                f'unit_serial={serial}: quantity {qty} exceeds remaining '
                f'{unit.quantity_remaining}',
            )

        if qty == unit.quantity_remaining:
            # Whole bag relocates; stock still in the bag at dest.
            unit.location_id = to_location_id
            fields = ['location_id']
        else:
            # Partial: bag stays at source with less left.
            unit.quantity_remaining = unit.quantity_remaining - qty
            if unit.quantity_remaining == 0:
                unit.status = StockUnitStatus.CONSUMED
            else:
                unit.status = StockUnitStatus.PARTIALLY_CONSUMED
            fields = ['quantity_remaining', 'status']

        unit.save(update_fields=fields)
        updated.append(unit)

    return list(
        StockUnit.objects
        .filter(pk__in=[u.pk for u in updated])
        .select_related('lot__product', 'unit', 'location')
        .order_by('id')
    )


@transaction.atomic
def apply_unit_moves_for_transfer_atomic(**kwargs) -> list[StockUnit]:
    return apply_unit_moves_for_transfer(**kwargs)
