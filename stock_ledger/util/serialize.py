from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from stock_ledger.models import StockBalance

BALANCE_SELECT_RELATED = (
    'location',
    'lot__product__product_class',
    'lot__product__range',
    'lot__product__unit',
    'lot__product__yield_data',
)


def _dec(value):
    return str(value) if value is not None else None


def serialize_balance_row(balance: StockBalance) -> dict:
    product = balance.lot.product
    try:
        yield_factor = _dec(product.yield_data.yield_factor)
    except ObjectDoesNotExist:
        yield_factor = None
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
