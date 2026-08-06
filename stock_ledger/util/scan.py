"""Resolve a scanned or typed code to a product (and a batch when we know it)."""

from __future__ import annotations

import re

from product.models import Product
from stock_ledger.models import StockUnit
from stock_ledger.util import stock_units
from stock_ledger.util.conversions import StockValidationError

# Bracketed GS1 AI form, e.g. (01)05012345678901(10)26218(21)ABC. Only needed so
# legacy per-unit serial labels keep scanning; product labels carry P<id> alone.
_AI_PATTERN = re.compile(r'\((\d{2,4})\)([^(]*)')
_PRODUCT_CODE = re.compile(r'^P?(\d+)$', re.IGNORECASE)


def parse_gs1(text: str) -> dict[str, str]:
    return {ai: value.strip() for ai, value in _AI_PATTERN.findall(text or '')}


def _product_by_code(text: str) -> Product | None:
    match = _PRODUCT_CODE.match(text)
    if match is None:
        return None
    return (
        Product.objects
        .select_related('unit', 'product_class', 'range')
        .filter(pk=int(match.group(1)))
        .first()
    )


def resolve_scan(code: str) -> dict:
    """
    Returns {'match_type', 'product', 'lot', 'unit'}.

    Product code wins so a hand-typed P123 always works, even if a label is
    damaged. Serial lookup is the fallback for legacy per-unit labels.
    """
    text = (code or '').strip()
    if not text:
        raise StockValidationError('code is required')

    serial = parse_gs1(text).get('21')
    if serial:
        unit = stock_units.get_unit_by_serial(serial)
        return {
            'match_type': 'unit_serial',
            'product': unit.lot.product,
            'lot': unit.lot,
            'unit': unit,
        }

    product = _product_by_code(text)
    if product is not None:
        return {
            'match_type': 'product',
            'product': product,
            'lot': None,
            'unit': None,
        }

    unit = (
        StockUnit.objects
        .select_related('lot__product__unit', 'lot__product__product_class',
                        'lot__product__range', 'location', 'unit')
        .filter(unit_serial=text)
        .first()
    )
    if unit is not None:
        return {
            'match_type': 'unit_serial',
            'product': unit.lot.product,
            'lot': unit.lot,
            'unit': unit,
        }

    raise StockValidationError(f'code={text} not found')
