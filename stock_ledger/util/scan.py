"""Resolve a scanned or typed code to a product (and a batch when we know it)."""

from __future__ import annotations

import re

from product.models import Product
from stock_ledger.models import StockEntry, StockLot, StockUnit
from stock_ledger.util import entry_labels, stock_units
from stock_ledger.util.conversions import StockValidationError

# Bracketed GS1 AI form, e.g. (01)05012345678901(10)26218(21)ABC. Only needed so
# legacy per-unit serial labels keep scanning; product labels carry P<id> alone.
_AI_PATTERN = re.compile(r'\((\d{2,4})\)([^(]*)')
_PRODUCT_CODE = re.compile(r'^P?(\d+)$', re.IGNORECASE)
# Production batch label: P{productId}T{trace} (e.g. P545T26218).
_PRODUCT_TRACE = re.compile(r'^P?(\d+)T(.+)$', re.IGNORECASE)


def parse_gs1(text: str) -> dict[str, str]:
    return {ai: value.strip() for ai, value in _AI_PATTERN.findall(text or '')}


def _product_by_id(product_id: int) -> Product | None:
    return (
        Product.objects
        .select_related(
            'unit',
            'product_class',
            'range',
            'destination_container',
        )
        .filter(pk=product_id)
        .first()
    )


def _product_by_code(text: str) -> Product | None:
    match = _PRODUCT_CODE.match(text)
    if match is None:
        return None
    return _product_by_id(int(match.group(1)))


def _entry_by_id(entry_id: int) -> StockEntry | None:
    return (
        StockEntry.objects
        .select_related(
            'lot__product__unit',
            'lot__product__product_class',
            'lot__product__range',
            'lot__product__destination_container',
            'unit',
            'location',
            'label',
        )
        .filter(pk=entry_id)
        .first()
    )


def resolve_scan(code: str) -> dict:
    """
    Returns {'match_type', 'product', 'lot', 'unit'[, 'entry']}.

    Product code wins so a hand-typed P123 always works, even if a label is
    damaged. Serial lookup is the fallback for legacy per-unit labels.
    Goods-in stickers use E{stock_entry.id}.
    """
    text = (code or '').strip()
    if not text:
        raise StockValidationError('code is required')
    if text.upper() in ('E', 'P'):
        raise StockValidationError(
            'Scan the full barcode. This code is incomplete.',
        )

    entry_id = entry_labels.parse_entry_code(text)
    if entry_id is not None:
        entry = _entry_by_id(entry_id)
        if entry is None:
            completed = entry_labels.resolve_truncated_entry_id(str(entry_id))
            entry = _entry_by_id(completed) if completed is not None else None
        if entry is None:
            raise StockValidationError(f'code={text} not found')
        return {
            'match_type': 'entry',
            'product': entry.lot.product,
            'lot': entry.lot,
            'unit': None,
            'entry': entry,
        }

    serial = parse_gs1(text).get('21')
    if serial:
        unit = stock_units.get_unit_by_serial(serial)
        return {
            'match_type': 'unit_serial',
            'product': unit.lot.product,
            'lot': unit.lot,
            'unit': unit,
        }

    pt = _PRODUCT_TRACE.match(text)
    if pt is not None:
        product = _product_by_id(int(pt.group(1)))
        if product is None:
            raise StockValidationError(f'code={text} not found')
        trace = pt.group(2).strip()
        lot = (
            StockLot.objects
            .filter(product_id=product.id, trace_number__iexact=trace)
            .order_by('id')
            .first()
        )
        return {
            'match_type': 'product_trace' if lot is not None else 'product',
            'product': product,
            'lot': lot,
            'unit': None,
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
