from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from hardware.services import codes_for_serials
from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.models import Product, ProductGoodsInType, ProductSupplier
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryType,
    StockReportEmailRecipient,
)
from stock_ledger.stream import iter_sse, subscribe
from stock_ledger.util.allocation_status import held_balance_keys
from stock_ledger.util.closing_stock_email import recipient_id_from_unsubscribe_token
from stock_ledger.util.conversions import preload_kg_factors, stock_to_kg
from stock_ledger.util.fifo import FIFO_ORDER
from stock_ledger.util.reports import (
    closing_balances_as_of,
    consolidate_closing_balances,
    goods_out_movements_report,
    movements_report,
    operator_activity_detail,
    operator_activity_report,
)
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    audit_event_dict,
    pack_breakdown_row,
    receipt_meta_by_lot_ids,
    serialize_balance_rows,
    supplier_pack_fields,
)
from stock_ledger.util.timeline import (
    consolidate_audit_items,
    expand_split_siblings,
    po_numbers_for_entries,
    posted_history_qs,
)
from stock_ledger.views.common import (
    _dec,
    _parse_clock,
    _parse_date,
    _parse_json_body,
)
from users_rbac.auth import attach_user
from users_rbac.permissions import require_any_admin


@csrf_exempt
@require_GET
def audit_timeline_api(request):
    """Stock audit timeline: all stock_entry rows, newest recorded_at first."""
    qs = (
        StockEntry.objects.select_related(
            'unit',
            'location',
            'counterparty_location',
            'lot__product',
            'lot__product_supplier__outer_unit',
            'posting',
            'label',
        )
        .order_by('-recorded_at', '-id')
    )
    try:
        product_id = request.GET.get('product_id')
        location_id = request.GET.get('location_id')
        lot_id = request.GET.get('lot_id')
        entry_type = request.GET.get('entry_type')
        source_document_type = request.GET.get('source_document_type')
        source_document_id = request.GET.get('source_document_id')
        actor_user_id = request.GET.get('actor_user_id')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        limit = request.GET.get('limit')
        offset = request.GET.get('offset')
        view = (request.GET.get('view') or 'detail').strip().lower()
        if view not in ('detail', 'consolidated'):
            raise ValueError('Invalid view. Use detail or consolidated.')

        if product_id not in (None, ''):
            qs = qs.filter(lot__product_id=int(product_id))
        if location_id not in (None, ''):
            lid = int(location_id)
            qs = qs.filter(
                models.Q(location_id=lid) | models.Q(counterparty_location_id=lid)
            )
        if lot_id not in (None, ''):
            qs = qs.filter(lot_id=int(lot_id))
        if entry_type not in (None, ''):
            qs = qs.filter(entry_type=entry_type)
        if source_document_type not in (None, ''):
            qs = qs.filter(source_document_type=source_document_type)
        if source_document_id not in (None, ''):
            qs = qs.filter(source_document_id=int(source_document_id))
        if actor_user_id not in (None, ''):
            qs = qs.filter(actor_user_id=int(actor_user_id))
        if date_from not in (None, ''):
            qs = qs.filter(recorded_at__date__gte=_parse_date(date_from, 'date_from'))
        if date_to not in (None, ''):
            qs = qs.filter(recorded_at__date__lte=_parse_date(date_to, 'date_to'))

        row_limit = int(limit) if limit not in (None, '') else 200
        row_limit = max(1, min(row_limit, 1000))
        row_offset = int(offset) if offset not in (None, '') else 0
        if row_offset < 0:
            raise ValueError('offset must be >= 0.')
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    count = qs.count()
    entries = list(qs[row_offset : row_offset + row_limit])
    page_ids = {entry.id for entry in entries}
    if view == 'consolidated':
        entries, page_ids = expand_split_siblings(qs, entries)
    device_codes = codes_for_serials(entry.device_serial for entry in entries)
    po_numbers = po_numbers_for_entries(entries)
    rows = [
        audit_event_dict(entry, device_codes, po_numbers) for entry in entries
    ]
    if view == 'consolidated':
        rows = consolidate_audit_items(rows, page_ids=page_ids)
    return api_success(
        'Stock audit timeline fetched.',
        {
            'items': rows,
            'count': count,
            'limit': row_limit,
            'offset': row_offset,
            'has_more': row_offset + row_limit < count,
            'order': 'recorded_at_desc',
            'view': view,
        },
    )


_GOODS_OUT_HISTORY_TYPES = (
    StockEntryType.ISSUE,
    StockEntryType.TRANSFER_OUT,
)


def _product_movement_history(
    request,
    product_id: int,
    *,
    entry_types: tuple[str, ...],
    kind: str,
    message: str,
):
    if not Product.objects.filter(pk=product_id).exists():
        return api_error('Product not found.', status_code=404)
    try:
        location_id = request.GET.get('location_id')
        loc_id = int(location_id) if location_id not in (None, '') else None
        limit = request.GET.get('limit')
        offset = request.GET.get('offset')
        row_limit = int(limit) if limit not in (None, '') else 200
        row_limit = max(1, min(row_limit, 1000))
        row_offset = int(offset) if offset not in (None, '') else 0
        if row_offset < 0:
            raise ValueError('offset must be >= 0.')
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    qs = posted_history_qs(
        product_id=product_id,
        entry_types=entry_types,
        location_id=loc_id,
    )
    count = qs.count()
    entries = list(qs[row_offset : row_offset + row_limit])
    entries, page_ids = expand_split_siblings(qs, entries)
    device_codes = codes_for_serials(entry.device_serial for entry in entries)
    po_numbers = po_numbers_for_entries(entries)
    rows = consolidate_audit_items(
        [audit_event_dict(entry, device_codes, po_numbers) for entry in entries],
        page_ids=page_ids,
    )
    return api_success(
        message,
        {
            'kind': kind,
            'items': rows,
            'count': count,
            'limit': row_limit,
            'offset': row_offset,
            'has_more': row_offset + row_limit < count,
            'order': 'recorded_at_desc',
        },
    )


@csrf_exempt
@require_GET
def product_goods_in_history_api(request, product_id: int):
    """Posted receipts for a product. Queued stickers stay on the Queue tab."""
    return _product_movement_history(
        request,
        product_id,
        entry_types=(StockEntryType.RECEIPT,),
        kind='goods_in',
        message='Goods in history fetched.',
    )


@csrf_exempt
@require_GET
def product_goods_out_history_api(request, product_id: int):
    """Posted issues and warehouse transfer_out. No transfer_in or recon."""
    return _product_movement_history(
        request,
        product_id,
        entry_types=_GOODS_OUT_HISTORY_TYPES,
        kind='goods_out',
        message='Goods out history fetched.',
    )


def _report_optional_filters(request):
    """Shared optional filters for stock report endpoints."""
    product_id = request.GET.get('product_id')
    location_id = request.GET.get('location_id')
    # Plan name product_type → model field goods_in_type (alias both).
    goods_in_type = request.GET.get('goods_in_type') or request.GET.get('product_type')

    parsed_product_id = None
    if product_id not in (None, ''):
        parsed_product_id = int(product_id)

    parsed_location_id = None
    if location_id not in (None, ''):
        parsed_location_id = int(location_id)

    if goods_in_type not in (None, ''):
        valid = {c.value for c in ProductGoodsInType}
        if goods_in_type not in valid:
            raise ValueError(
                f'Invalid goods_in_type. Use one of: {", ".join(sorted(valid))}.'
            )

    return parsed_product_id, goods_in_type or None, parsed_location_id


@csrf_exempt
@require_GET
def goods_in_report_api(request):
    """Goods in (receipt) movements by effective_at date range."""
    try:
        date_from = _parse_date(request.GET.get('date_from'), 'date_from')
        date_to = _parse_date(request.GET.get('date_to'), 'date_to')
        if date_from is None or date_to is None:
            return api_error('date_from and date_to are required (YYYY-MM-DD).')
        if date_from > date_to:
            return api_error('date_from must be on or before date_to.')
        product_id, goods_in_type, location_id = _report_optional_filters(request)
        limit = request.GET.get('limit')
        row_limit = int(limit) if limit not in (None, '') else 200
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    rows = movements_report(
        entry_type=StockEntryType.RECEIPT,
        date_from=date_from,
        date_to=date_to,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
        limit=row_limit,
    )
    return api_success(
        'Goods in report fetched.',
        {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'count': len(rows),
            'results': rows,
        },
    )


@csrf_exempt
@require_GET
def goods_out_report_api(request):
    """Goods out (issue) movements by effective_at date range."""
    try:
        date_from = _parse_date(request.GET.get('date_from'), 'date_from')
        date_to = _parse_date(request.GET.get('date_to'), 'date_to')
        if date_from is None or date_to is None:
            return api_error('date_from and date_to are required (YYYY-MM-DD).')
        if date_from > date_to:
            return api_error('date_from must be on or before date_to.')
        product_id, goods_in_type, location_id = _report_optional_filters(request)
        limit = request.GET.get('limit')
        row_limit = int(limit) if limit not in (None, '') else 200
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    rows = goods_out_movements_report(
        date_from=date_from,
        date_to=date_to,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
        limit=row_limit,
    )
    return api_success(
        'Goods out report fetched.',
        {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'count': len(rows),
            'results': rows,
        },
    )


@csrf_exempt
@require_GET
def closing_stock_report_api(request):
    """Closing stock balances as of end of selected calendar day."""
    try:
        as_of = _parse_date(request.GET.get('as_of'), 'as_of')
        if as_of is None:
            return api_error('as_of is required (YYYY-MM-DD).')
        product_id, goods_in_type, location_id = _report_optional_filters(request)
        include_zero = str(request.GET.get('include_zero', '')).lower() in (
            '1', 'true', 'yes',
        )
        view = (request.GET.get('view') or 'detail').strip().lower()
        if view not in ('detail', 'consolidated'):
            raise ValueError('Invalid view. Use detail or consolidated.')
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    rows = closing_balances_as_of(
        as_of=as_of,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
        include_zero=include_zero,
    )
    if view == 'consolidated':
        rows = consolidate_closing_balances(rows)
    return api_success(
        'Closing stock report fetched.',
        {
            'as_of': as_of.isoformat(),
            'view': view,
            'group_by': 'product_shape' if view == 'consolidated' else None,
            'count': len(rows),
            'results': rows,
        },
    )


def _operator_activity_window(request):
    day = _parse_date(request.GET.get('date'), 'date')
    if day is None:
        raise ValueError('date is required (YYYY-MM-DD).')
    from_time = _parse_clock(request.GET.get('from_time'), 'from_time')
    to_time = _parse_clock(request.GET.get('to_time'), 'to_time')
    if from_time is not None and to_time is not None and from_time > to_time:
        raise ValueError('from_time must be on or before to_time.')
    return day, from_time, to_time


@csrf_exempt
@require_GET
def operator_activity_report_api(request):
    """Manager day overview: operators, entries, scans, stock-in queue."""
    try:
        day, from_time, to_time = _operator_activity_window(request)
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    data = operator_activity_report(
        day=day,
        from_time=from_time,
        to_time=to_time,
    )
    return api_success('Operator activity fetched.', data)


@csrf_exempt
@require_GET
def operator_activity_detail_api(request):
    """Drill-down: one operator's stock entries in the day window."""
    try:
        day, from_time, to_time = _operator_activity_window(request)
        user_id = request.GET.get('user_id')
        if user_id in (None, ''):
            return api_error('user_id is required.')
        parsed_user_id = int(user_id)
        limit = request.GET.get('limit')
        row_limit = int(limit) if limit not in (None, '') else 200
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    data = operator_activity_detail(
        day=day,
        user_id=parsed_user_id,
        from_time=from_time,
        to_time=to_time,
        limit=row_limit,
    )
    return api_success('Operator activity detail fetched.', data)


def _recipient_dict(row: StockReportEmailRecipient) -> dict:
    return {
        'id': row.id,
        'email': row.email,
        'is_active': row.is_active,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _gate_report_recipients(request):
    denied = attach_user(request)
    if denied:
        return denied
    return require_any_admin(request)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def report_email_recipients_api(request):
    """List or add closing-stock report email recipients (admin)."""
    denied = _gate_report_recipients(request)
    if denied:
        return denied

    if request.method == 'GET':
        rows = StockReportEmailRecipient.objects.all()
        return api_success(
            'Report email recipients fetched.',
            [_recipient_dict(row) for row in rows],
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid request body.')
    email = (body.get('email') or '').strip().lower()
    if not email:
        return api_error('email is required.')
    try:
        validate_email(email)
    except ValidationError:
        return api_error('Enter a valid email address.')
    if StockReportEmailRecipient.objects.filter(email__iexact=email).exists():
        return api_error('That email is already on the list.', status_code=409)
    row = StockReportEmailRecipient.objects.create(email=email)
    return api_success(
        'Report email recipient added.',
        _recipient_dict(row),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def report_email_recipient_detail_api(request, pk: int):
    """Update or remove a closing-stock report email recipient (admin)."""
    denied = _gate_report_recipients(request)
    if denied:
        return denied

    row = StockReportEmailRecipient.objects.filter(pk=pk).first()
    if row is None:
        return api_error("We couldn't find that recipient.", status_code=404)

    if request.method == 'DELETE':
        row.delete()
        return api_success('Report email recipient removed.', {'ref': pk})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid request body.')
    if 'is_active' not in body:
        return api_error('is_active is required.')
    row.is_active = bool(body['is_active'])
    if body.get('email'):
        email = str(body['email']).strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            return api_error('Enter a valid email address.')
        clash = (
            StockReportEmailRecipient.objects.filter(email__iexact=email)
            .exclude(pk=row.pk)
            .exists()
        )
        if clash:
            return api_error('That email is already on the list.', status_code=409)
        row.email = email
    row.save()
    return api_success('Report email recipient updated.', _recipient_dict(row))


@csrf_exempt
@require_GET
def report_email_unsubscribe_api(request):
    """One-click unsubscribe from closing-stock emails (no auth; signed token)."""
    token = request.GET.get('token') or ''
    try:
        pk = recipient_id_from_unsubscribe_token(token)
    except ValueError:
        return HttpResponse(
            '<html><body><p>This unsubscribe link is not valid.</p></body></html>',
            status=400,
            content_type='text/html; charset=utf-8',
        )
    row = StockReportEmailRecipient.objects.filter(pk=pk).first()
    if row is None:
        return HttpResponse(
            '<html><body><p>You are already unsubscribed.</p></body></html>',
            content_type='text/html; charset=utf-8',
        )
    if row.is_active:
        row.is_active = False
        row.save(update_fields=['is_active', 'updated_at'])
    return HttpResponse(
        '<html><body><p>You have been unsubscribed from closing stock emails.</p>'
        '<p>An admin can re-enable you in Stock report settings.</p></body></html>',
        content_type='text/html; charset=utf-8',
    )


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
    data = serialize_balance_rows(rows, receipt_meta=receipt_meta)
    return api_success('Balances fetched.', data)


@csrf_exempt
@require_GET
def warehouse_remaining_api(request):
    """
    Remaining stock for production warehouse (storage locations), unit-wise.
    Optional ?location_id= to filter one unit (e.g. Unit 2 / Unit 11).
    Aggregates lots → remaining_qty per product per location.
    """
    preload_kg_factors()
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

    breakdowns: dict[tuple[int, int], list] = {}
    loose_kg_by_key: dict[tuple[int, int], Decimal] = {}
    for bal in (
        qs.select_related(
            'lot__product__unit',
            'lot__product_supplier__outer_unit',
            'lot__product_supplier__inner_unit',
        )
        .order_by('location_id', 'lot__product_id', *FIFO_ORDER)
    ):
        key = (bal.location_id, bal.lot.product_id)
        product = bal.lot.product
        row = pack_breakdown_row(
            bal.quantity,
            product,
            getattr(bal.lot, 'product_supplier', None),
            lot_id=bal.lot_id,
            trace_number=bal.lot.trace_number,
        )
        if row is not None:
            breakdowns.setdefault(key, []).append(row)
            continue
        kg = stock_to_kg(bal.quantity, product) if product is not None else None
        if kg is not None:
            loose_kg_by_key[key] = loose_kg_by_key.get(key, Decimal('0')) + kg

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
        key = (loc_id, pid)
        pack = supplier_pack_fields(
            qty, products.get(pid), mappings.get(pid),
        )
        loose = loose_kg_by_key.get(key)
        bucket['products'].append({
            'product_id': pid,
            'product_name': row['lot__product__name'],
            'unit_id': row['lot__product__unit_id'],
            'unit_name': row['lot__product__unit__name'],
            'remaining_qty': _dec(qty),
            'lot_count': row['lot_count'],
            **pack,
            'pack_breakdown': breakdowns.get(key, []),
            'loose_kg': _dec(loose) if loose else None,
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
