import json
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from stock_ledger.models import StockBalance, StockEntry, StockLot, StockReservation
from stock_ledger.util import reservations, services
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.trace import (
    mass_balance_for_output,
    trace_backward,
    trace_forward,
)


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _dec(value):
    return str(value) if value is not None else None


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field_name}.') from exc


def _parse_effective_at(value):
    if value in (None, ''):
        return timezone.now()
    dt = parse_datetime(str(value))
    if dt is None:
        raise ValueError('Invalid effective_at. Use ISO-8601 datetime.')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def entry_dict(entry: StockEntry) -> dict:
    return {
        'id': entry.id,
        'idempotency_key': entry.idempotency_key,
        'entry_type': entry.entry_type,
        'lot_id': entry.lot_id,
        'location_id': entry.location_id,
        'counterparty_location_id': entry.counterparty_location_id,
        'transfer_group_id': entry.transfer_group_id,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'base_unit_factor': _dec(entry.base_unit_factor),
        'quantity_base': _dec(entry.quantity_base),
        'period_id': entry.period_id,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'reverses_entry_id': entry.reverses_entry_id,
        'entry_hash': entry.entry_hash,
        'prev_hash': entry.prev_hash,
    }


def reservation_dict(row: StockReservation) -> dict:
    return {
        'id': row.id,
        'lot_id': row.lot_id,
        'location_id': row.location_id,
        'quantity': _dec(row.quantity),
        'unit_id': row.unit_id,
        'status': row.status,
        'source_document_type': row.source_document_type,
        'source_document_id': row.source_document_id,
        'source_document_line': row.source_document_line,
        'consumed_by_entry_id': row.consumed_by_entry_id,
        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
    }


def _require_lot(lot_id) -> StockLot:
    try:
        return StockLot.objects.get(pk=lot_id)
    except StockLot.DoesNotExist as exc:
        raise StockValidationError(f'lot_id={lot_id} not found') from exc


def _common_write_kwargs(body: dict) -> dict:
    kwargs = {
        'override_reason': body.get('override_reason'),
        'authorised_by_user_id': body.get('authorised_by_user_id'),
        'actor_user_id': body.get('actor_user_id'),
        'remarks': body.get('remarks'),
        'source_document_type': body.get('source_document_type'),
        'source_document_id': body.get('source_document_id'),
        'source_document_line': body.get('source_document_line'),
    }
    return {k: v for k, v in kwargs.items() if v is not None}


@csrf_exempt
@require_http_methods(['POST'])
def receipt_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.receipt(
            idempotency_key=body['idempotency_key'],
            lot=_require_lot(body['lot_id']),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            effective_at=_parse_effective_at(body.get('effective_at')),
            unit_cost=(
                _parse_decimal(body['unit_cost'], 'unit_cost')
                if body.get('unit_cost') not in (None, '')
                else None
            ),
            **_common_write_kwargs(body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Receipt posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def issue_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.issue(
            idempotency_key=body['idempotency_key'],
            lot=_require_lot(body['lot_id']),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Issue posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def transfer_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        out_entry, in_entry = services.transfer(
            idempotency_key=body['idempotency_key'],
            lot=_require_lot(body['lot_id']),
            from_location_id=int(body['from_location_id']),
            to_location_id=int(body['to_location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Transfer posted.',
        {'out': entry_dict(out_entry), 'in': entry_dict(in_entry)},
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['POST'])
def disposal_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.disposal(
            idempotency_key=body['idempotency_key'],
            lot=_require_lot(body['lot_id']),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Disposal posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def count_adjustment_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.count_adjustment(
            idempotency_key=body['idempotency_key'],
            lot=_require_lot(body['lot_id']),
            location_id=int(body['location_id']),
            quantity_delta=_parse_decimal(body['quantity_delta'], 'quantity_delta'),
            unit_id=int(body['unit_id']),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Count adjustment posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def reversal_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source = StockEntry.objects.get(pk=body['entry_id'])
        entry = services.reversal(
            idempotency_key=body['idempotency_key'],
            entry=source,
            effective_at=_parse_effective_at(body.get('effective_at')),
            actor_user_id=body.get('actor_user_id'),
            **_common_write_kwargs(body),
        )
    except StockEntry.DoesNotExist:
        return api_error('entry_id not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Reversal posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_GET
def entry_detail_api(request, pk: int):
    try:
        entry = StockEntry.objects.get(pk=pk)
    except StockEntry.DoesNotExist:
        return api_error('Entry not found.', status_code=404)
    return api_success('Entry fetched.', entry_dict(entry))


@csrf_exempt
@require_GET
def balance_list_api(request):
    qs = StockBalance.objects.all().order_by('lot_id', 'location_id')
    lot_id = request.GET.get('lot_id')
    location_id = request.GET.get('location_id')
    if lot_id:
        qs = qs.filter(lot_id=lot_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    data = [
        {
            'lot_id': b.lot_id,
            'location_id': b.location_id,
            'quantity': _dec(b.quantity),
            'quantity_base': _dec(b.quantity_base),
            'last_entry_id': b.last_entry_id,
            'updated_at': b.updated_at.isoformat() if b.updated_at else None,
        }
        for b in qs[:500]
    ]
    return api_success('Balances fetched.', data)


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
            lot=_require_lot(body['lot_id']),
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
