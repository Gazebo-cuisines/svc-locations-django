from django.db import models
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from stock_ledger.models import StockEntry, StockEntryPostingStatus
from stock_ledger.util import entry_labels, entry_posting
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.parse import parse_date as _parse_date
from stock_ledger.util.payloads import audit_event_dict, entry_dict
from stock_ledger.views._common import _common_write_kwargs, _parse_json_body
from users_rbac.permissions import gate_warehouse_write


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
    """Goods IN label payload for an entry (barcode E{id})."""
    try:
        entry = entry_labels.get_entry_for_label(entry_id)
    except StockValidationError as exc:
        return api_error(str(exc), status_code=404)
    label = entry_labels.get_label(entry)
    data = {
        'goods_in_label': entry_labels.build_goods_in_label(entry, label),
    }
    if label is not None:
        data['label'] = entry_labels.label_state_dict(label)
    return api_success('Entry label ready.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def entry_label_print_api(request, entry_id: int):
    """Mark Goods IN labels printed; returns print payload."""
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
    return api_success(
        'Entry labels marked printed.',
        {
            'label': entry_labels.label_state_dict(label),
            'goods_in_label': entry_labels.build_goods_in_label(
                label.stock_entry, label,
            ),
        },
    )


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
    """Goods-in inbox: receipts waiting for label confirm + stock post."""
    try:
        limit = int(request.GET.get('limit') or 100)
    except (TypeError, ValueError):
        return api_error('limit must be an integer.')
    rows = entry_posting.list_queued_receipts(limit=limit)
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
            row['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
        results.append(row)
    return api_success(
        'Queued receipts fetched.',
        {'count': len(results), 'results': results},
    )

@csrf_exempt
@require_GET
def audit_timeline_api(request):
    """Stock audit timeline: who/what/when from immutable stock_entry rows."""
    qs = (
        StockEntry.objects.select_related(
            'unit',
            'location',
            'counterparty_location',
            'lot__product',
        )
        .exclude(
            posting__status__in=[
                StockEntryPostingStatus.QUEUED,
                StockEntryPostingStatus.CANCELLED,
            ],
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
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    rows = [audit_event_dict(entry) for entry in qs[:row_limit]]
    return api_success('Stock audit timeline fetched.', rows)
