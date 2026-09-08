from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from hardware.services import touch_from_request
from locations.utils.api_response import api_error, api_success
from product.models import Product
from stock_ledger.models import StockBalance, StockEntry, StockEntryType, StockReservation
from stock_ledger.util import entry_labels, investigate, reservations, scan, stickers, stock_units
from stock_ledger.util.conversions import StockValidationError, stock_to_kg, stock_to_packs
from stock_ledger.util.product_supplier_lookup import product_supplier_for_lot
from stock_ledger.util.recall import (
    RecallLookupError,
    build_product_genealogy_index,
    build_recall_report,
)
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    entry_dict,
    reservation_dict,
    supplier_pack_fields,
)
from stock_ledger.util.trace import mass_balance_for_output, trace_backward, trace_forward
from stock_ledger.views.common import (
    _dec,
    _fifo_batch_rows,
    _parse_date,
    _parse_decimal,
    _parse_json_body,
    _product_destination_fields,
    _resolve_lot,
)


@csrf_exempt
@require_GET
def atp_api(request):
    try:
        lot_id = int(request.GET['lot_id'])
        location_id = int(request.GET['location_id'])
    except (KeyError, TypeError, ValueError):
        return api_error('lot_id and location_id query params are required.')
    qty = reservations.available_to_promise(lot_id=lot_id, location_id=location_id)
    return api_success(
        'ATP fetched.',
        {'lot_id': lot_id, 'location_id': location_id, 'available_to_promise': _dec(qty)},
    )


@csrf_exempt
@require_GET
def recall_api(request):
    """Complaint / recall: product_id + use_by → all matching lots + genealogy."""
    product_raw = request.GET.get('product_id')
    use_by_raw = request.GET.get('use_by')
    if product_raw in (None, ''):
        return api_error('product_id query param is required.')
    if use_by_raw in (None, ''):
        return api_error('use_by query param is required.')
    try:
        product_id = int(product_raw)
    except (TypeError, ValueError):
        return api_error('product_id must be an integer.')
    try:
        use_by = _parse_date(use_by_raw, 'use_by')
    except ValueError as exc:
        return api_error(str(exc))
    if use_by is None:
        return api_error('use_by query param is required.')
    try:
        data = build_recall_report(product_id=product_id, use_by=use_by)
    except RecallLookupError as exc:
        return api_error(str(exc), status_code=exc.status_code)
    return api_success('Recall report fetched.', data)


@csrf_exempt
@require_GET
def product_genealogy_api(request, product_id: int):
    """All lots for a product with genealogy trees (or index-only)."""
    with_trees_raw = (request.GET.get('with_trees') or '1').strip().lower()
    if with_trees_raw in ('0', 'false', 'no'):
        with_trees = False
    elif with_trees_raw in ('1', 'true', 'yes'):
        with_trees = True
    else:
        return api_error('with_trees must be 0 or 1.')
    try:
        data = build_product_genealogy_index(
            product_id=product_id,
            with_trees=with_trees,
        )
    except RecallLookupError as exc:
        return api_error(str(exc), status_code=exc.status_code)
    return api_success('Product genealogy fetched.', data)


@csrf_exempt
@require_GET
def trace_backward_api(request):
    try:
        lot_id = int(request.GET['lot_id'])
    except (KeyError, TypeError, ValueError):
        return api_error('lot_id query param is required.')
    rows = trace_backward(lot_id=lot_id)
    for row in rows:
        if isinstance(row.get('quantity_base'), Decimal):
            row['quantity_base'] = _dec(row['quantity_base'])
    return api_success('Backward trace fetched.', rows)


@csrf_exempt
@require_GET
def trace_forward_api(request):
    try:
        lot_id = int(request.GET['lot_id'])
    except (KeyError, TypeError, ValueError):
        return api_error('lot_id query param is required.')
    rows = trace_forward(lot_id=lot_id)
    for row in rows:
        if isinstance(row.get('quantity_base'), Decimal):
            row['quantity_base'] = _dec(row['quantity_base'])
    return api_success('Forward trace fetched.', rows)


@csrf_exempt
@require_GET
def mass_balance_api(request, entry_id: int):
    try:
        data = mass_balance_for_output(output_entry_id=entry_id)
    except StockValidationError as exc:
        return api_error(str(exc), status_code=404)
    for key in ('inputs_quantity_base', 'output_quantity_base', 'yield_loss'):
        if isinstance(data.get(key), Decimal):
            data[key] = _dec(data[key])
    return api_success('Mass balance fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
def reservation_create_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        row = reservations.reserve(
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            source_document_type=body.get('source_document_type'),
            source_document_id=body.get('source_document_id'),
            source_document_line=body.get('source_document_line'),
            allow_over_reserve=bool(body.get('allow_over_reserve', False)),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Reservation created.', reservation_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def reservation_release_api(request, pk: int):
    try:
        row = StockReservation.objects.get(pk=pk)
        row = reservations.release(row)
    except StockReservation.DoesNotExist:
        return api_error('Reservation not found.', status_code=404)
    except StockValidationError as exc:
        return api_error(str(exc))
    return api_success('Reservation released.', reservation_dict(row))


@csrf_exempt
@require_http_methods(['POST'])
def reservation_consume_api(request, pk: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        reservation = StockReservation.objects.get(pk=pk)
        entry = StockEntry.objects.get(pk=body['entry_id'])
        row = reservations.consume(reservation, entry=entry)
    except StockReservation.DoesNotExist:
        return api_error('Reservation not found.', status_code=404)
    except StockEntry.DoesNotExist:
        return api_error('entry_id not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        return api_error(str(exc))
    return api_success('Reservation consumed.', reservation_dict(row))

def _flag(request, name: str) -> bool:
    return str(request.GET.get(name, '')).lower() in ('1', 'true', 'yes')


def _fifo_check_error(match, loc_id, batches, code):
    if loc_id is None:
        return api_error('location_id is required when check_fifo=1.')
    lot = match.get('lot')
    if lot is None:
        return api_error(
            'Scan the goods-in or batch label, not the product barcode.',
        )
    scanned = (code or '').strip()
    if not batches:
        return api_error(
            'No stock for this item at this location.',
            data={'error': 'no_stock', 'scanned_code': scanned},
            status_code=409,
        )
    recommended = batches[0]
    if lot.id == recommended['lot_id']:
        return None
    rec_trace = recommended.get('trace_number')
    rec_use_by = recommended.get('use_by')
    use_by_bit = f' (use by {rec_use_by})' if rec_use_by else ''
    return api_error(
        f'Please use older stock first. Scan trace {rec_trace}{use_by_bit}, '
        f'or override.',
        data={
            'error': 'fifo_mismatch',
            'scanned_lot_id': lot.id,
            'scanned_trace': lot.trace_number,
            'scanned_code': scanned,
            'recommended_lot_id': recommended['lot_id'],
            'recommended_trace': rec_trace,
            'recommended_use_by': rec_use_by,
        },
        status_code=409,
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
    mapping = product_supplier_for_lot(match.get('lot'))
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
def investigate_api(request):
    """Read-only bag/product incident pack + briefing."""
    try:
        data = investigate.investigate(
            code=request.GET.get('code'),
            recipe_code=request.GET.get('recipe_code'),
            product_id=request.GET.get('product_id'),
            q=request.GET.get('q'),
            date=request.GET.get('date'),
            location_id=request.GET.get('location_id'),
        )
    except investigate.InvestigateError as exc:
        return api_error(str(exc), status_code=exc.status_code)
    return api_success('Ledger investigate ready.', data)


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

    entry = match.get('entry')
    if entry is not None and entry.entry_type in (
        StockEntryType.ISSUE,
        StockEntryType.TRANSFER_OUT,
    ):
        return api_error(
            'You have scanned a Goods OUT barcode. '
            'Please scan the Goods IN barcode.',
            data={
                'error': 'goods_out_barcode',
                'scanned_code': (code or '').strip(),
            },
            status_code=409,
        )

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
    dest = _product_destination_fields(product)
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
            'destination_container_id': dest['destination_container_id'],
            'destination_container_name': dest['destination_container_name'],
        },
        'lot_id': lot.id,
        'trace_number': lot.trace_number,
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'production_date': (
            lot.production_date.isoformat() if lot.production_date else None
        ),
        'location_id': loc_id,
        'from_location_id': loc_id,
        'to_location_id': dest['to_location_id'],
        'to_location_name': dest['to_location_name'],
        'dest_ok': dest['dest_ok'],
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
    if not dest['dest_ok']:
        data['dest_message'] = (
            'This product has no destination location. '
            'Fix product master before Goods Out without plan.'
        )
    if not fifo_ok and oldest is not None:
        data['recommended_lot_id'] = oldest['lot_id']
        data['recommended_trace'] = oldest.get('trace_number')
        data['recommended_use_by'] = oldest.get('use_by')
    touch_from_request(request, action='scan', location_id=loc_id)
    return api_success('Scan resolved.', data)
