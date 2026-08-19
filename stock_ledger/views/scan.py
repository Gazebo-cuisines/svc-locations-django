from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from hardware.services import touch_from_request
from locations.utils.api_response import api_error, api_success
from product.models import Product
from stock_ledger.models import StockBalance
from stock_ledger.util import entry_labels, scan, stickers, stock_units
from stock_ledger.util.conversions import StockValidationError, stock_to_kg, stock_to_packs
from stock_ledger.util.payloads import _dec, entry_dict
from stock_ledger.util.serialize import BALANCE_SELECT_RELATED, supplier_pack_fields
from stock_ledger.views._common import (
    _fifo_batch_rows,
    _fifo_check_error,
    _flag,
    _product_supplier_for_lot,
)


@csrf_exempt
@require_GET
def scan_resolve_api(request):
    """Scan or type a product / entry / unit code, get stock detail."""
    code = request.GET.get('code')
    location_id = request.GET.get('location_id')
    try:
        loc_id = int(location_id) if location_id not in (None, '') else None
    except (TypeError, ValueError):
        return api_error('location_id must be an integer.')

    try:
        match = scan.resolve_scan(code)
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)

    product = match['product']
    expected_raw = request.GET.get('expected_product_id')
    if expected_raw not in (None, ''):
        try:
            expected_id = int(expected_raw)
        except (TypeError, ValueError):
            return api_error('expected_product_id must be an integer.')
        if product.id != expected_id:
            expected = (
                Product.objects.filter(pk=expected_id).only('id', 'name').first()
            )
            expected_name = expected.name if expected else f'product {expected_id}'
            scanned_name = product.name
            return api_error(
                f'Please scan the barcode for {expected_name}. '
                f'The one you scanned is for {scanned_name}.',
                data={
                    'error': 'wrong_product',
                    'expected_product_id': expected_id,
                    'expected_product_name': expected_name,
                    'scanned_product_id': product.id,
                    'scanned_product_name': scanned_name,
                    'scanned_code': (code or '').strip(),
                },
                status_code=409,
            )
    include_incomplete = _flag(request, 'include_incomplete')
    batches = _fifo_batch_rows(
        product_id=product.id,
        location_id=loc_id,
        include_incomplete=include_incomplete,
    )
    check_fifo = _flag(request, 'check_fifo')
    if check_fifo:
        fifo_err = _fifo_check_error(match, loc_id, batches, code)
        if fifo_err is not None:
            return fifo_err
        selected = next(
            row for row in batches if row['lot_id'] == match['lot'].id
        )
        total = selected['quantity']
        batches = []
    else:
        total = (
            sum(Decimal(row['quantity']) for row in batches)
            if batches else Decimal('0')
        )

    data = {
        'scanned_code': (code or '').strip(),
        'match_type': match['match_type'],
        'selected_lot_id': match['lot'].id if match['lot'] is not None else None,
        'location_id': loc_id,
        'product': {
            'product_id': product.id,
            'product_code': stock_units.product_code(product),
            'name': product.name,
            'recipe_code': product.recipe_code,
            'unit_id': product.unit_id,
            'unit_name': product.unit.name if product.unit_id else None,
            'product_class_id': product.product_class_id,
            'range_id': product.range_id,
            'label_mode': product.label_mode,
            'is_active': product.is_active,
        },
        'total_quantity': str(total),
        'batch_count': len(batches),
        'batches': batches,
    }
    stock_qty = Decimal(str(total or 0))
    data['display_kg'] = _dec(stock_to_kg(stock_qty, product))
    mapping = _product_supplier_for_lot(match.get('lot'))
    if mapping is not None:
        try:
            data['pack_quantity'] = _dec(
                stock_to_packs(stock_qty, mapping, product),
            )
        except StockValidationError:
            data['pack_quantity'] = None
        data['pack_unit_name'] = (
            mapping.outer_unit.name if mapping.outer_unit_id else None
        )
        data['shape_format_label'] = mapping.shape_format_label
    else:
        data['pack_quantity'] = None
        data['pack_unit_name'] = None
        data['shape_format_label'] = None
    if check_fifo and match.get('lot') is not None:
        lot = match['lot']
        data['trace_number'] = lot.trace_number
        data['use_by'] = lot.use_by.isoformat() if lot.use_by else None
    entry = match.get('entry')
    if entry is not None:
        data['entry'] = entry_dict(entry)
        label = entry_labels.get_label(entry)
        data['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
        if label is not None:
            data['label'] = entry_labels.label_state_dict(label)
    touch_from_request(request, action='scan', location_id=loc_id)
    return api_success('Scan resolved.', data)


@csrf_exempt
@require_GET
def scan_goods_out_api(request):
    """Scan a bag for outbound pick: lot qty + FIFO warning, never a FIFO 409."""
    code = request.GET.get('code')
    location_id = request.GET.get('location_id')
    if location_id in (None, ''):
        return api_error('location_id is required.')
    try:
        loc_id = int(location_id)
    except (TypeError, ValueError):
        return api_error('location_id must be an integer.')

    try:
        match = scan.resolve_scan(code)
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)

    lot = match.get('lot')
    if lot is None:
        return api_error('Scan the bag label, not the product barcode.')

    product = match['product']
    expected_raw = request.GET.get('expected_product_id')
    if expected_raw not in (None, ''):
        try:
            expected_id = int(expected_raw)
        except (TypeError, ValueError):
            return api_error('expected_product_id must be an integer.')
        if product.id != expected_id:
            expected = (
                Product.objects.filter(pk=expected_id).only('id', 'name').first()
            )
            expected_name = expected.name if expected else f'product {expected_id}'
            return api_error(
                f'Please scan the barcode for {expected_name}. '
                f'The one you scanned is for {product.name}.',
                data={
                    'error': 'wrong_product',
                    'expected_product_id': expected_id,
                    'expected_product_name': expected_name,
                    'scanned_product_id': product.id,
                    'scanned_product_name': product.name,
                    'scanned_code': (code or '').strip(),
                },
                status_code=409,
            )

    balance = (
        StockBalance.objects
        .select_related(*BALANCE_SELECT_RELATED)
        .filter(lot_id=lot.id, location_id=loc_id, quantity__gt=0)
        .first()
    )
    if balance is None:
        return api_error(
            'No stock for this bag at this location.',
            data={
                'error': 'no_stock',
                'lot_id': lot.id,
                'scanned_code': (code or '').strip(),
            },
            status_code=409,
        )

    lot_qty = balance.quantity
    unit = match.get('unit')
    entry = match.get('entry')
    sticker_initial = None
    queued_draws = []
    if unit is not None:
        qty = unit.quantity_remaining
        sticker_initial = unit.quantity_initial
    elif entry is not None:
        qty = stickers.remaining_for_entry(entry, lot_quantity=lot_qty)
        sticker_initial = abs(entry.quantity)
        queued_draws = stickers.queued_draws_for_entry(entry)
    else:
        qty = lot_qty
    pack = supplier_pack_fields(
        qty, product, getattr(lot, 'product_supplier', None),
    )
    batches = _fifo_batch_rows(product_id=product.id, location_id=loc_id)
    oldest = batches[0] if batches else None
    fifo_ok = oldest is not None and oldest['lot_id'] == lot.id
    data = {
        'scanned_code': (code or '').strip(),
        'match_type': match['match_type'],
        'entry_id': entry.id if entry is not None else None,
        'entry_code': (
            entry_labels.entry_code(entry.id) if entry is not None else None
        ),
        'product': {
            'product_id': product.id,
            'name': product.name,
            'recipe_code': product.recipe_code,
            'unit': product.unit.name if product.unit_id else None,
        },
        'lot_id': lot.id,
        'trace_number': lot.trace_number,
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'production_date': (
            lot.production_date.isoformat() if lot.production_date else None
        ),
        'location_id': loc_id,
        'quantity': _dec(qty),
        'sticker_initial': _dec(sticker_initial),
        'lot_quantity': _dec(lot_qty),
        **pack,
        'fifo_ok': fifo_ok,
        # Why the sticker reads lower than the lot: stock committed by an
        # earlier pick that has not posted yet.
        'queued_quantity': _dec(
            sum((row['quantity'] for row in queued_draws), Decimal('0')),
        ),
        'queued_draws': [
            {**row, 'quantity': _dec(row['quantity'])} for row in queued_draws
        ],
    }
    if not fifo_ok and oldest is not None:
        data['recommended_lot_id'] = oldest['lot_id']
        data['recommended_trace'] = oldest.get('trace_number')
        data['recommended_use_by'] = oldest.get('use_by')
    touch_from_request(request, action='scan', location_id=loc_id)
    return api_success('Scan resolved.', data)
