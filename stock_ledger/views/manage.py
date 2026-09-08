from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from stock_ledger.models import StockEntry
from stock_ledger.util import entry_labels, manage
from stock_ledger.util.manage import ManageRemoveError
from stock_ledger.views.common import _common_write_kwargs, _parse_date, _parse_json_body
from users_rbac.permissions import gate_stock_management


@csrf_exempt
@require_GET
@gate_stock_management
def manage_ping_api(request):
    """Health check for Stock Management Tool access (gated)."""
    return api_success('Stock Management Tool access OK.', {'ok': True})


@csrf_exempt
@require_GET
@gate_stock_management
def manage_entries_list_api(request):
    """Manager grid: searchable entry list, newest first."""
    try:
        product_id = request.GET.get('product_id')
        location_id = request.GET.get('location_id')
        entry_type = request.GET.get('entry_type')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        code = (request.GET.get('code') or '').strip()
        limit = int(request.GET.get('limit') or 50)
        offset = int(request.GET.get('offset') or 0)
        limit = max(1, min(limit, 200))
        if offset < 0:
            raise ValueError('offset must be >= 0.')

        parsed_product_id = int(product_id) if product_id not in (None, '') else None
        parsed_location_id = int(location_id) if location_id not in (None, '') else None
        parsed_date_from = (
            _parse_date(date_from, 'date_from') if date_from not in (None, '') else None
        )
        parsed_date_to = (
            _parse_date(date_to, 'date_to') if date_to not in (None, '') else None
        )
        entry_id = entry_labels.parse_entry_code(code) if code else None
        if entry_id is None and code.upper().startswith('E') and code[1:].isdigit():
            entry_id = int(code[1:])
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    data = manage.list_manage_entries(
        product_id=parsed_product_id,
        location_id=parsed_location_id,
        entry_type=entry_type if entry_type not in (None, '') else None,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
        entry_id=entry_id,
        limit=limit,
        offset=offset,
    )
    return api_success('Manage entries fetched.', data)


@csrf_exempt
@require_GET
@gate_stock_management
def manage_entry_preview_api(request, entry_id: int):
    """Preview what removing an entry would undo (read-only)."""
    try:
        entry = manage.get_entry_for_manage(entry_id)
    except StockEntry.DoesNotExist:
        return api_error('Entry not found.', status_code=404)
    data = manage.build_manage_detail(entry)
    return api_success('Entry remove preview ready.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_stock_management
def manage_entry_remove_api(request, entry_id: int):
    """Cancel a queued (unposted) entry and void its labels."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        audit = _common_write_kwargs(request, body)
        result = manage.remove_entry(
            entry_id=entry_id,
            reason=body['reason'],
            idempotency_key=body['idempotency_key'],
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockEntry.DoesNotExist:
        return api_error('Entry not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except ManageRemoveError as exc:
        return api_error(str(exc), status_code=exc.status_code)
    message = (
        'Entry was already removed.'
        if result.get('idempotent')
        else 'Removed. Complete the checklist, then bin old stickers.'
    )
    return api_success(message, result, status_code=201)
