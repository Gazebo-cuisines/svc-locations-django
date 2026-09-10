from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Product
from stock_ledger.models import StockBalance, StockEntry, StockEntryType, StockLot
from stock_ledger.util import entry_labels, entry_posting, stock_units
from stock_ledger.util.conversions import StockValidationError, preload_kg_factors
from stock_ledger.util.serialize import entry_dict, lot_dict, stock_unit_dict
from stock_ledger.util.trace import trace_backward, trace_forward
from stock_ledger.views.common import (
    _common_write_kwargs,
    _dec,
    _parse_decimal,
    _parse_effective_at,
    _parse_json_body,
)
from users_rbac.permissions import gate_floor_write, gate_warehouse_write


@csrf_exempt
@require_GET
def entry_detail_api(request, pk: int):
    related = (
        'lot__product',
        'lot__shape_format',
        'unit',
        'location',
        'counterparty_location',
        'label',
        'posting',
    )
    try:
        entry = StockEntry.objects.select_related(*related).get(pk=pk)
    except StockEntry.DoesNotExist:
        return api_error('Entry not found.', status_code=404)
    data = entry_dict(entry)
    label = entry_labels.get_label(entry)
    if label is not None:
        data['label'] = entry_labels.label_state_dict(label)
        data['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
    posting = entry_posting.get_posting(entry)
    if posting is not None:
        data['posting'] = entry_posting.posting_dict(posting)
        data['posting_status'] = posting.status
    if entry.transfer_group_id:
        siblings = (
            StockEntry.objects
            .select_related(*related)
            .filter(transfer_group_id=entry.transfer_group_id)
            .exclude(pk=entry.pk)
            .order_by('id')
        )
        data['related_entries'] = [entry_dict(s) for s in siblings]
    return api_success('Entry fetched.', data)


@csrf_exempt
@require_GET
def entry_label_api(request, entry_id: int):
    """Label payload for reprint (E{id}): OUT for transfer_out, else Goods IN."""
    try:
        entry = entry_labels.get_entry_for_label(entry_id)
    except StockValidationError as exc:
        return api_error(str(exc), status_code=404)
    label = entry_labels.get_label(entry)
    data = entry_labels.artwork_fields(entry, label)
    if label is not None:
        data['label'] = entry_labels.label_state_dict(label)
    return api_success('Entry label ready.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def entry_label_print_api(request, entry_id: int):
    """Print or reprint entry label (same E{id}); bumps printed_count."""
    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        audit = _common_write_kwargs(request, body)
        if body.get('label_format') not in (None, ''):
            entry = entry_labels.get_entry_for_label(entry_id)
            entry_labels.create_entry_label(
                entry=entry,
                label_format=body.get('label_format'),
                label_count=body.get('label_count'),
                actor_user_id=audit.get('actor_user_id'),
                lan_username=audit.get('lan_username'),
                source_workstation=audit.get('source_workstation'),
            )
        label = entry_labels.mark_printed(
            entry_id=entry_id,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    data = entry_labels.artwork_fields(label.stock_entry, label)
    data['label'] = entry_labels.label_state_dict(label)
    return api_success('Entry labels marked printed.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def entry_label_verify_api(request, entry_id: int):
    """Scan applied sticker to confirm it matches E{entry_id}."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        audit = _common_write_kwargs(request, body)
        result = entry_labels.verify_label(
            entry_id=entry_id,
            code=str(body['code']),
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
            meta=body.get('meta') if isinstance(body.get('meta'), dict) else None,
        )
        post_flag = body.get('post_stock') in (True, 'true', '1', 'yes', 'True')
        if post_flag and result.get('label', {}).get('status') == 'verified':
            posted = entry_posting.post_entry(
                entry_id=entry_id,
                require_label_verified=True,
                actor_user_id=audit.get('actor_user_id'),
                lan_username=audit.get('lan_username'),
                source_workstation=audit.get('source_workstation'),
            )
            result['posting'] = posted.get('posting')
            result['posting_status'] = posted.get('status')
            result['stock_posted'] = not posted.get('already_live', False) or (
                posted.get('status') == 'posted'
            )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    return api_success('Label verified.', result)


@csrf_exempt
@require_GET
def entry_label_activity_api(request, entry_id: int):
    """Label scan timeline for a goods-in entry."""
    try:
        data = entry_labels.list_label_activity(entry_id)
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    return api_success('Label activity fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def entry_post_api(request, entry_id: int):
    """Apply queued receipt to stock_balance after label verify (hard gate)."""
    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        audit = _common_write_kwargs(request, body)
        # Optional scan-in-the-same-call before post.
        if body.get('code') not in (None, ''):
            entry_labels.verify_label(
                entry_id=entry_id,
                code=str(body['code']),
                actor_user_id=audit.get('actor_user_id'),
                lan_username=audit.get('lan_username'),
                source_workstation=audit.get('source_workstation'),
            )
        result = entry_posting.post_entry(
            entry_id=entry_id,
            require_label_verified=body.get('require_label_verified', True) is not False,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
        entry = (
            StockEntry.objects
            .select_related(
                'lot__product', 'lot__shape_format', 'unit', 'location',
                'counterparty_location', 'label', 'posting',
            )
            .get(pk=entry_id)
        )
        result['entry'] = entry_dict(entry)
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    return api_success('Entry posted to stock.', result)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def entry_cancel_api(request, entry_id: int):
    """Drop a queued posting so remaining qty is no longer reserved."""
    try:
        posting = entry_posting.cancel_entry(entry_id=entry_id)
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    return api_success('Entry cancelled.', entry_posting.posting_dict(posting))


@csrf_exempt
@require_GET
def entry_queued_list_api(request):
    """Inbox: queued receipts and transfer_out waiting for print/verify/post."""
    try:
        limit = int(request.GET.get('limit') or 100)
        offset = int(request.GET.get('offset') or 0)
        entry_type = request.GET.get('entry_type') or None
        raw_src = request.GET.get('source_document_id')
        raw_loc = request.GET.get('location_id')
        raw_product = request.GET.get('product_id')
        source_document_id = (
            int(raw_src) if raw_src not in (None, '') else None
        )
        location_id = int(raw_loc) if raw_loc not in (None, '') else None
        product_id = (
            int(raw_product) if raw_product not in (None, '') else None
        )
    except (TypeError, ValueError):
        return api_error(
            'limit, offset, source_document_id, location_id, and product_id must be integers.',
        )
    if entry_type and entry_type not in (
        StockEntryType.RECEIPT,
        StockEntryType.TRANSFER_OUT,
    ):
        return api_error('entry_type must be receipt or transfer_out.')
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    preload_kg_factors()
    total = entry_posting.queued_receipts_qs(
        entry_type=entry_type,
        source_document_id=source_document_id,
        location_id=location_id,
        product_id=product_id,
    ).count()
    rows = entry_posting.list_queued_receipts(
        limit=limit,
        offset=offset,
        entry_type=entry_type,
        source_document_id=source_document_id,
        location_id=location_id,
        product_id=product_id,
    )
    results = []
    for entry in rows:
        row = entry_dict(entry)
        posting = entry_posting.get_posting(entry)
        if posting is not None:
            row['posting'] = entry_posting.posting_dict(posting)
            row['posting_status'] = posting.status
        label = entry_labels.get_label(entry)
        if label is not None:
            row['label'] = entry_labels.label_state_dict(label)
        row['steps'] = entry_posting.queued_step_flags(entry)
        if entry.entry_type == StockEntryType.TRANSFER_OUT:
            row['goods_out_label'] = entry_labels.build_goods_out_label(
                issue_entry=entry,
                copies=label.label_count if label is not None else 1,
                label=label,
            )
        elif label is not None:
            row['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
        results.append(row)
    return api_success(
        'Queued receipts fetched.',
        {
            'count': len(results),
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(results) < total,
            'results': results,
        },
    )

@csrf_exempt
@require_GET
def product_label_api(request, product_id: int):
    """Reusable product label: product id barcode + name / use by / trace text."""
    product = (
        Product.objects
        .select_related('unit')
        .filter(pk=product_id)
        .first()
    )
    if product is None:
        return api_error(f'product_id={product_id} not found.', status_code=404)

    lot = None
    lot_id = request.GET.get('lot_id')
    if lot_id not in (None, ''):
        try:
            lot = StockLot.objects.filter(
                pk=int(lot_id), product_id=product_id,
            ).first()
        except (TypeError, ValueError):
            return api_error('lot_id must be an integer.')
        if lot is None:
            return api_error(
                f'lot_id={lot_id} not found for this product.',
                status_code=404,
            )

    return api_success(
        'Product label ready.',
        stock_units.build_product_label(product=product, lot=lot),
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_print_api(request):
    """Print physical labels against a receipt or production_output entry."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source_entry = StockEntry.objects.select_related(
            'lot__product', 'unit', 'location',
        ).get(pk=body['entry_id'])
        audit = _common_write_kwargs(request, body)
        units = stock_units.create_units_for_entry(
            source_entry=source_entry,
            unit_count=int(body['unit_count']),
            quantity_per_unit=_parse_decimal(
                body['quantity_per_unit'], 'quantity_per_unit',
            ),
            idempotency_key_prefix=str(body['idempotency_key_prefix']),
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockEntry.DoesNotExist:
        return api_error('entry_id not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Stock units printed.',
        [stock_unit_dict(u) for u in units],
        status_code=201,
    )


@csrf_exempt
@require_GET
def stock_units_detail_api(request, unit_serial: str):
    """Scan lookup: resolve a physical unit serial to product/stock info."""
    try:
        unit = stock_units.get_unit_by_serial(unit_serial)
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)

    balance = (
        StockBalance.objects
        .filter(lot_id=unit.lot_id, location_id=unit.location_id)
        .only('quantity', 'quantity_base', 'updated_at')
        .first()
    )
    product = unit.lot.product
    data = stock_unit_dict(unit)
    data['product'] = {
        'id': product.id,
        'name': product.name,
        'external_barcode': product.external_barcode,
    }
    data['lot'] = lot_dict(unit.lot)
    data['location'] = {
        'id': unit.location_id,
        'name': unit.location.name,
    }
    data['unit'] = {
        'id': unit.unit_id,
        'name': unit.unit.name,
    }
    data['location_balance'] = (
        {
            'quantity': _dec(balance.quantity),
            'quantity_base': _dec(balance.quantity_base),
            'updated_at': (
                balance.updated_at.isoformat() if balance.updated_at else None
            ),
        }
        if balance is not None
        else None
    )

    trace_mode = (request.GET.get('trace') or '').strip().lower()
    if trace_mode in ('backward', 'forward'):
        rows = (
            trace_backward(lot_id=unit.lot_id)
            if trace_mode == 'backward'
            else trace_forward(lot_id=unit.lot_id)
        )
        for row in rows:
            if isinstance(row.get('quantity_base'), Decimal):
                row['quantity_base'] = _dec(row['quantity_base'])
        data['trace'] = rows
    elif trace_mode:
        return api_error('trace must be backward or forward.')

    return api_success('Stock unit fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_floor_write
def stock_units_consume_api(request, unit_serial: str):
    """Scan-to-consume: issue / disposal / production_consumption via unit serial."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        result = stock_units.consume_unit(
            unit_serial=unit_serial,
            entry_type=str(body['entry_type']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            idempotency_key=body['idempotency_key'],
            output_entry_id=(
                int(body['output_entry_id'])
                if body.get('output_entry_id') not in (None, '')
                else None
            ),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    except (ValueError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Stock unit consumed.',
        {
            'entry': entry_dict(result['entry']),
            'unit': stock_unit_dict(result['unit']),
            'consumption_id': result['consumption'].id,
        },
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_void_api(request, unit_serial: str):
    """Void a damaged or misprinted label (no stock_balance change)."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        audit = _common_write_kwargs(request, body)
        unit = stock_units.void_unit(
            unit_serial=unit_serial,
            reason=str(body['reason']),
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    return api_success('Stock unit voided.', stock_unit_dict(unit))


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_reprint_api(request, unit_serial: str):
    """Reprint same serial; records a print event for audit."""
    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        audit = _common_write_kwargs(request, body)
        result = stock_units.reprint_unit(
            unit_serial=unit_serial,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    data = stock_unit_dict(result['unit'])
    data['print_event_id'] = result['print_event_id']
    return api_success('Stock unit reprint recorded.', data, status_code=201)
