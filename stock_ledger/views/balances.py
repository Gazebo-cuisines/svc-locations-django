from decimal import Decimal

from django.db import models
from django.http import StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.models import Product, ProductSupplier
from stock_ledger.models import StockBalance, StockEntry, StockReservation
from stock_ledger.stream import iter_sse, subscribe
from stock_ledger.util import reservations
from stock_ledger.util.allocation_status import held_balance_keys
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.fifo import FIFO_ORDER
from stock_ledger.util.parse import parse_date as _parse_date, parse_decimal as _parse_decimal
from stock_ledger.util.payloads import _dec, reservation_dict
from stock_ledger.util.recall import (
    RecallLookupError,
    build_product_genealogy_index,
    build_recall_report,
)
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    receipt_meta_by_lot_ids,
    serialize_balance_row,
    supplier_pack_fields,
)
from stock_ledger.util.trace import mass_balance_for_output, trace_backward, trace_forward
from stock_ledger.views._common import _parse_json_body, _resolve_lot


@csrf_exempt
@require_GET
def balance_list_api(request):
    qs = StockBalance.objects.filter(lot__product__is_active=True).select_related(
        *BALANCE_SELECT_RELATED
    ).order_by(
        'lot_id', 'location_id'
    )
    lot_id = request.GET.get('lot_id')
    location_id = request.GET.get('location_id')
    product_id = request.GET.get('product_id')
    trace_number = request.GET.get('trace_number')
    use_by = request.GET.get('use_by')
    include_zero = str(request.GET.get('include_zero', '')).lower() in (
        '1', 'true', 'yes',
    )
    if not include_zero:
        qs = qs.filter(quantity__gt=0)
    if lot_id:
        qs = qs.filter(lot_id=lot_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    if product_id not in (None, ''):
        try:
            qs = qs.filter(lot__product_id=int(product_id))
        except (TypeError, ValueError):
            return api_error('product_id must be an integer.')
    if trace_number not in (None, ''):
        qs = qs.filter(lot__trace_number=trace_number)
    if use_by not in (None, ''):
        try:
            qs = qs.filter(lot__use_by=_parse_date(use_by, 'use_by'))
        except ValueError as exc:
            return api_error(str(exc))

    order = str(request.GET.get('order', '')).lower()
    if order == 'fifo':
        qs = qs.order_by(*FIFO_ORDER)
    elif order not in ('', 'default'):
        return api_error('Invalid order. Use fifo or default.')

    include_incomplete = str(request.GET.get('include_incomplete', '')).lower() in (
        '1', 'true', 'yes',
    )
    if location_id not in (None, ''):
        try:
            int(location_id)
        except (TypeError, ValueError):
            return api_error('location_id must be an integer.')
    rows = list(qs[:500])
    held = held_balance_keys(rows, include_incomplete=include_incomplete)
    if held:
        rows = [b for b in rows if (b.location_id, b.lot_id) not in held]

    receipt_meta = receipt_meta_by_lot_ids({b.lot_id for b in rows})
    data = [
        serialize_balance_row(b, receipt_meta=receipt_meta.get(b.lot_id))
        for b in rows
    ]
    return api_success('Balances fetched.', data)


@csrf_exempt
@require_GET
def warehouse_remaining_api(request):
    """
    Remaining stock for production warehouse (storage locations), unit-wise.
    Optional ?location_id= to filter one unit (e.g. Unit 2 / Unit 11).
    Aggregates lots → remaining_qty per product per location.
    """
    qs = (
        StockBalance.objects
        .filter(
            quantity__gt=0,
            lot__product__is_active=True,
            location__roles__role=LocationRole.STORAGE,
        )
        .distinct()
    )
    location_id = request.GET.get('location_id')
    if location_id not in (None, ''):
        try:
            loc_id = int(location_id)
        except (TypeError, ValueError):
            return api_error('location_id must be an integer.')
        if not Location.objects.filter(
            pk=loc_id, roles__role=LocationRole.STORAGE,
        ).exists():
            return api_error(
                f'location_id={loc_id} is not a storage warehouse.',
                status_code=404,
            )
        qs = qs.filter(location_id=loc_id)

    rows = (
        qs.values(
            'location_id',
            'location__name',
            'lot__product_id',
            'lot__product__name',
            'lot__product__unit_id',
            'lot__product__unit__name',
        )
        .annotate(
            remaining_qty=models.Sum('quantity'),
            lot_count=models.Count('lot_id', distinct=True),
        )
        .order_by('location__name', 'lot__product__name')
    )
    rows = list(rows)
    product_ids = {row['lot__product_id'] for row in rows}
    products = {
        p.id: p
        for p in Product.objects.filter(pk__in=product_ids).select_related('unit')
    }
    mappings = {}
    for mapping in (
        ProductSupplier.objects
        .filter(product_id__in=product_ids, is_active=True)
        .select_related('outer_unit', 'inner_unit')
        .order_by('-is_default', '-id')
    ):
        mappings.setdefault(mapping.product_id, mapping)

    by_location: dict[int, dict] = {}
    for row in rows:
        loc_id = row['location_id']
        bucket = by_location.get(loc_id)
        if bucket is None:
            bucket = {
                'location_id': loc_id,
                'location_name': row['location__name'],
                'products': [],
            }
            by_location[loc_id] = bucket
        qty = row['remaining_qty']
        pid = row['lot__product_id']
        pack = supplier_pack_fields(
            qty, products.get(pid), mappings.get(pid),
        )
        bucket['products'].append({
            'product_id': pid,
            'product_name': row['lot__product__name'],
            'unit_id': row['lot__product__unit_id'],
            'unit_name': row['lot__product__unit__name'],
            'remaining_qty': _dec(qty),
            'lot_count': row['lot_count'],
            **pack,
        })

    data = list(by_location.values())
    return api_success('Warehouse remaining stock fetched.', data)


@csrf_exempt
@require_GET
def balance_stream_api(request):
    # Holds a gunicorn sync worker for the connection lifetime — fine for a few
    # overview tabs; use gevent/ASGI if many concurrent streams.
    q = subscribe()
    response = StreamingHttpResponse(
        iter_sse(q),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


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
