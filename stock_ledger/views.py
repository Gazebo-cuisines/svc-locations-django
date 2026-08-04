import base64
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import models
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Product, PurchaseShapeFormat, Unit
from product.query import active_products
from recipe.models import RecipeVersion
from stock_ledger.models import (
    ProductionRun,
    StockBalance,
    StockEntry,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockReservation,
    StockUnitConversion,
)
from stock_ledger.stream import iter_sse, subscribe
from stock_ledger.util import reservations, services
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.serialize import BALANCE_SELECT_RELATED, serialize_balance_row
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


def _parse_date(value, field_name: str):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.') from exc


def _format_display_date(value) -> str | None:
    """YYYY-MM-DD / date → '03 Aug 2026' for UI lists."""
    if value in (None, ''):
        return None
    if isinstance(value, date):
        d = value
    else:
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return d.strftime('%d %b %Y')


def lot_dict(lot: StockLot) -> dict:
    return {
        'id': lot.id,
        'product_id': lot.product_id,
        'recipe_version_id': lot.recipe_version_id,
        'shape_format_id': lot.shape_format_id,
        'trace_number': lot.trace_number,
        'supplier_lot_code': lot.supplier_lot_code,
        'origin': lot.origin,
        'production_date': (
            lot.production_date.isoformat() if lot.production_date else None
        ),
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'created_at': lot.created_at.isoformat() if lot.created_at else None,
    }


def unit_conversion_dict(row: StockUnitConversion) -> dict:
    return {
        'id': row.id,
        'unit_id': row.unit_id,
        'product_id': row.product_id,
        'to_kg': _dec(row.to_kg),
        'source': row.source,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


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
        'po_number': entry.po_number,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'source_document_line': entry.source_document_line,
        'remarks': entry.remarks,
        'entry_hash': entry.entry_hash,
        'prev_hash': entry.prev_hash,
    }


def audit_event_dict(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product
    return {
        'entry_id': entry.id,
        'at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'action': entry.entry_type,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'unit_name': entry.unit.name if entry.unit_id else None,
        'product_id': product.id,
        'product_name': product.name,
        'lot_id': lot.id,
        'trace_number': lot.trace_number,
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'location_id': entry.location_id,
        'location_name': entry.location.name if entry.location_id else None,
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            entry.counterparty_location.name
            if entry.counterparty_location_id
            else None
        ),
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'source_document_line': entry.source_document_line,
        'po_number': entry.po_number,
        'remarks': entry.remarks,
        'reverses_entry_id': entry.reverses_entry_id,
        'actor_user_id': entry.actor_user_id,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
    }


def production_run_dict(run: ProductionRun) -> dict:
    return {
        'id': run.id,
        'stock_entry_id': run.stock_entry_id,
        'resource_id': run.resource_id,
        'shift_code': run.shift_code,
        'staff_count': run.staff_count,
        'base_date': run.base_date.isoformat() if run.base_date else None,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'created_at': run.created_at.isoformat() if run.created_at else None,
    }


def production_list_row(run: ProductionRun) -> dict:
    entry = run.stock_entry
    lot = entry.lot
    product = lot.product
    return {
        'entry_id': entry.id,
        'run_id': run.id,
        'base_date': _format_display_date(run.base_date),
        'base_date_iso': run.base_date.isoformat() if run.base_date else None,
        'from_location_id': entry.counterparty_location_id,
        'from_location_name': (
            entry.counterparty_location.name
            if entry.counterparty_location_id
            else None
        ),
        'to_location_id': entry.location_id,
        'to_location_name': entry.location.name if entry.location_id else None,
        'product_id': product.id,
        'product_name': product.name,
        'recipe_code': product.recipe_code,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'unit_name': entry.unit.name if entry.unit_id else None,
        'resource_id': run.resource_id,
        'resource_name': run.resource.name if run.resource_id else None,
        'shift_code': run.shift_code,
        'staff_count': run.staff_count,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'use_by': _format_display_date(lot.use_by),
        'use_by_iso': lot.use_by.isoformat() if lot.use_by else None,
        'trace_number': lot.trace_number,
        'recipe_version_id': lot.recipe_version_id,
    }


def _optional_int_param(raw, field_name: str):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer.') from exc


def _list_production_runs(request):
    """Calendar/grid list: from→to locations + date / date range."""
    try:
        from_id = _optional_int_param(
            request.GET.get('from_location_id')
            or request.GET.get('source_container_id')
            or request.GET.get('counterparty_location_id'),
            'from_location_id',
        )
        to_id = _optional_int_param(
            request.GET.get('to_location_id')
            or request.GET.get('destination_container_id')
            or request.GET.get('location_id'),
            'to_location_id',
        )
        day = _parse_date(request.GET.get('date'), 'date')
        date_from = _parse_date(request.GET.get('date_from'), 'date_from')
        date_to = _parse_date(request.GET.get('date_to'), 'date_to')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    qs = (
        ProductionRun.objects.filter(
            stock_entry__entry_type=StockEntryType.PRODUCTION_OUTPUT,
            stock_entry__reversed_by__isnull=True,
        )
        .select_related(
            'resource',
            'stock_entry__location',
            'stock_entry__counterparty_location',
            'stock_entry__unit',
            'stock_entry__lot__product',
        )
        .order_by('base_date', 'started_at', 'id')
    )
    if from_id is not None:
        qs = qs.filter(stock_entry__counterparty_location_id=from_id)
    if to_id is not None:
        qs = qs.filter(stock_entry__location_id=to_id)
    if day is not None:
        qs = qs.filter(base_date=day)
    else:
        if date_from is not None:
            qs = qs.filter(base_date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(base_date__lte=date_to)

    return api_success(
        'Production entries fetched.',
        [production_list_row(run) for run in qs[:500]],
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def production_api(request):
    """Floor production: GET list (calendar/dept) or POST stock-in + run sidecar."""
    if request.method == 'GET':
        return _list_production_runs(request)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry, run = _write_production(request, body)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Production posted.',
        {'entry': entry_dict(entry), 'run': production_run_dict(run)},
        status_code=201,
    )


def _write_production(request, body: dict, *, replace_entry_id: int | None = None):
    if body.get('origin') in (None, ''):
        body = {**body, 'origin': StockLotOrigin.PRODUCTION}
    lot = _resolve_lot(body)
    location_id = body.get('location_id')
    if location_id in (None, ''):
        location_id = lot.product.destination_container_id
    if location_id in (None, ''):
        raise StockValidationError(
            'location_id required (or set product.destination_container)'
        )
    resource_id = body.get('resource_id')
    if resource_id in (None, ''):
        raise StockValidationError('resource_id is required')

    base_date = _parse_date(body.get('base_date'), 'base_date')
    if base_date is None:
        production_date = _parse_date(body.get('production_date'), 'production_date')
        base_date = production_date or timezone.localdate()

    staff_count = body.get('staff_count')
    if staff_count not in (None, ''):
        staff_count = int(staff_count)
        if staff_count < 0:
            raise StockValidationError('staff_count must be >= 0')
    else:
        staff_count = None

    unit_id = body.get('unit_id')
    kwargs = dict(
        idempotency_key=body['idempotency_key'],
        lot=lot,
        location_id=int(location_id),
        quantity=_parse_decimal(body['quantity'], 'quantity'),
        resource_id=int(resource_id),
        base_date=base_date,
        unit_id=int(unit_id) if unit_id not in (None, '') else None,
        counterparty_location_id=(
            int(body['counterparty_location_id'])
            if body.get('counterparty_location_id') not in (None, '')
            else None
        ),
        shift_code=(
            str(body['shift_code'])
            if body.get('shift_code') not in (None, '')
            else None
        ),
        staff_count=staff_count,
        started_at=(
            _parse_effective_at(body.get('started_at'))
            if body.get('started_at') not in (None, '')
            else None
        ),
        finished_at=(
            _parse_effective_at(body.get('finished_at'))
            if body.get('finished_at') not in (None, '')
            else None
        ),
        effective_at=_parse_effective_at(body.get('effective_at')),
        **_common_write_kwargs(request, body),
    )
    if replace_entry_id is not None:
        return services.production_replace(entry_id=replace_entry_id, **kwargs)
    return services.production_output(**kwargs)


@csrf_exempt
@require_http_methods(['PUT', 'DELETE'])
def production_detail_api(request, entry_id: int):
    """PUT = reverse+recreate (edit). DELETE = reverse only."""
    if request.method == 'DELETE':
        body = _parse_json_body(request) or {}
        try:
            entry = services.production_void(
                entry_id=entry_id,
                idempotency_key=(
                    body.get('idempotency_key')
                    or f'reverse-production:{entry_id}'
                ),
                effective_at=_parse_effective_at(body.get('effective_at')),
                actor_user_id=body.get('actor_user_id'),
                **_common_write_kwargs(request, body),
            )
        except (ValueError, StockValidationError, TypeError) as exc:
            return api_error(str(exc))
        return api_success('Production reversed.', entry_dict(entry), status_code=201)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry, run = _write_production(request, body, replace_entry_id=entry_id)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Production updated.',
        {
            'entry': entry_dict(entry),
            'run': production_run_dict(run),
            'replaced_entry_id': entry_id,
        },
        status_code=201,
    )


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
        lot = StockLot.objects.select_related('product').get(pk=lot_id)
    except StockLot.DoesNotExist as exc:
        raise StockValidationError(f'lot_id={lot_id} not found') from exc
    if not lot.product.is_active:
        raise StockValidationError(f'lot_id={lot_id} product is inactive')
    return lot


def _resolve_lot(body: dict) -> StockLot:
    """lot_id OR product_id + soft attrs (use_by / trace / …)."""
    lot_id = body.get('lot_id')
    if lot_id not in (None, ''):
        return _require_lot(lot_id)

    product_id = body.get('product_id')
    if product_id in (None, ''):
        raise StockValidationError('lot_id or product_id is required')

    lot = services.resolve_lot(
        product_id=int(product_id),
        trace_number=body.get('trace_number') or None,
        use_by=_parse_date(body.get('use_by'), 'use_by'),
        production_date=_parse_date(body.get('production_date'), 'production_date'),
        recipe_version_id=body.get('recipe_version_id') or None,
        shape_format_id=body.get('shape_format_id') or None,
        origin=body.get('origin') or StockLotOrigin.PURCHASE,
        supplier_lot_code=body.get('supplier_lot_code') or None,
    )
    return StockLot.objects.select_related('product').get(pk=lot.pk)


def _optional_unit_id(body: dict) -> int | None:
    raw = body.get('unit_id')
    if raw in (None, ''):
        return None
    return int(raw)


def _decode_bearer_claims(request) -> dict:
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return {}
    token = auth_header.split(' ', 1)[1].strip()
    parts = token.split('.')
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = '=' * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode('utf-8')
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def _common_write_kwargs(request, body: dict) -> dict:
    claims = _decode_bearer_claims(request)
    lan_username = body.get('lan_username') or claims.get('sub')
    source_workstation = (
        body.get('source_workstation') or request.META.get('HTTP_USER_AGENT')
    )
    source_workstation_ip = (
        body.get('source_workstation_ip') or request.META.get('REMOTE_ADDR')
    )

    if lan_username is not None:
        lan_username = str(lan_username)[:64]
    if source_workstation is not None:
        source_workstation = str(source_workstation)[:64]
    if source_workstation_ip is not None:
        source_workstation_ip = str(source_workstation_ip)[:45]

    kwargs = {
        'override_reason': body.get('override_reason'),
        'authorised_by_user_id': body.get('authorised_by_user_id'),
        'actor_user_id': body.get('actor_user_id'),
        # Cognito-first: store stable subject in lan_username when explicit value is absent.
        'lan_username': lan_username,
        'source_workstation': source_workstation,
        'source_workstation_ip': source_workstation_ip,
        'remarks': body.get('remarks'),
        'source_document_type': body.get('source_document_type'),
        'source_document_id': body.get('source_document_id'),
        'source_document_line': body.get('source_document_line'),
        'po_number': body.get('po_number'),
    }
    po_number = body.get('po_number')
    if po_number not in (None, ''):
        po_number = str(po_number).strip()
        kwargs['po_number'] = po_number
        if kwargs.get('source_document_type') in (None, ''):
            kwargs['source_document_type'] = 'po'
        if kwargs.get('source_document_id') in (None, '') and po_number.isdigit():
            kwargs['source_document_id'] = int(po_number)
    return {k: v for k, v in kwargs.items() if v is not None}


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def lots_collection_api(request):
    if request.method == 'GET':
        qs = StockLot.objects.filter(product__is_active=True).order_by('-id')
        product_id = request.GET.get('product_id')
        if product_id not in (None, ''):
            try:
                qs = qs.filter(product_id=int(product_id))
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        return api_success('Lots fetched.', [lot_dict(lot) for lot in qs[:500]])

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')

    required = ['product_id', 'origin']
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(f'Missing required field: {missing[0]}')

    origin = body['origin']
    if origin not in StockLotOrigin.values:
        return api_error(
            f'Invalid origin. Use one of: {", ".join(StockLotOrigin.values)}',
        )

    product_id = body['product_id']
    if not active_products().filter(pk=product_id).exists():
        return api_error(f'product_id={product_id} not found.', status_code=404)

    recipe_version_id = body.get('recipe_version_id')
    if (
        recipe_version_id not in (None, '')
        and not RecipeVersion.objects.filter(pk=recipe_version_id).exists()
    ):
        return api_error(
            f'recipe_version_id={recipe_version_id} not found.',
            status_code=400,
        )

    shape_format_id = body.get('shape_format_id')
    if (
        shape_format_id not in (None, '')
        and not PurchaseShapeFormat.objects.filter(pk=shape_format_id).exists()
    ):
        return api_error(
            f'shape_format_id={shape_format_id} not found.',
            status_code=400,
        )

    try:
        production_date = _parse_date(body.get('production_date'), 'production_date')
        use_by = _parse_date(body.get('use_by'), 'use_by')
        trace_date = _parse_date(body.get('trace_date'), 'trace_date')
        trace_number = body.get('trace_number')
        if trace_number in (None, '') and production_date is None and trace_date is not None:
            production_date = trace_date

        lot = services.resolve_lot(
            product_id=product_id,
            trace_number=trace_number or None,
            use_by=use_by,
            production_date=production_date,
            recipe_version_id=recipe_version_id or None,
            shape_format_id=shape_format_id or None,
            origin=origin,
            supplier_lot_code=body.get('supplier_lot_code') or None,
        )
    except ValueError as exc:
        return api_error(str(exc))
    except StockValidationError as exc:
        return api_error(str(exc))

    return api_success('Lot created.', lot_dict(lot), status_code=201)


@csrf_exempt
@require_GET
def lot_detail_api(request, pk: int):
    try:
        lot = StockLot.objects.get(pk=pk)
    except StockLot.DoesNotExist:
        return api_error('Lot not found.', status_code=404)
    return api_success('Lot fetched.', lot_dict(lot))


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def unit_conversions_api(request):
    if request.method == 'GET':
        qs = StockUnitConversion.objects.all().order_by('unit_id', 'product_id')
        product_id = request.GET.get('product_id')
        unit_id = request.GET.get('unit_id')
        if product_id not in (None, ''):
            try:
                qs = qs.filter(product_id=int(product_id))
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        if unit_id not in (None, ''):
            try:
                qs = qs.filter(unit_id=int(unit_id))
            except (TypeError, ValueError):
                return api_error('unit_id must be an integer.')
        return api_success(
            'Unit conversions fetched.',
            [unit_conversion_dict(row) for row in qs[:500]],
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')

    required = ['unit_id', 'to_kg']
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(f'Missing required field: {missing[0]}')

    unit_id = body['unit_id']
    if not Unit.objects.filter(pk=unit_id).exists():
        return api_error(f'unit_id={unit_id} not found.', status_code=404)

    product_id = body.get('product_id')
    if product_id not in (None, ''):
        if not active_products().filter(pk=product_id).exists():
            return api_error(f'product_id={product_id} not found.', status_code=404)
    else:
        product_id = None

    try:
        to_kg = _parse_decimal(body['to_kg'], 'to_kg')
        if to_kg <= 0:
            return api_error('to_kg must be greater than 0.')
    except ValueError as exc:
        return api_error(str(exc))

    row, _created = StockUnitConversion.objects.update_or_create(
        unit_id=unit_id,
        product_id=product_id,
        defaults={
            'to_kg': to_kg,
            'source': body.get('source') or 'manual',
        },
    )
    return api_success('Unit conversion saved.', unit_conversion_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def downtime_api(request):
    """Record downtime time-row (qty 0, no stock). GET lists downtime product types."""
    if request.method == 'GET':
        rows = [
            {
                'id': p.id,
                'name': p.name,
                'unit_id': p.unit_id,
                'unit_name': p.unit.name if p.unit_id else None,
            }
            for p in (
                active_products()
                .filter(is_downtime=True)
                .select_related('unit')
                .order_by('name')
            )
        ]
        return api_success('Downtime types fetched.', rows)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        if body.get('origin') in (None, ''):
            body = {**body, 'origin': StockLotOrigin.PRODUCTION}
        lot = _resolve_lot(body)
        location_id = body.get('location_id')
        if location_id in (None, ''):
            raise StockValidationError('location_id is required')
        resource_id = body.get('resource_id')
        if resource_id in (None, ''):
            raise StockValidationError('resource_id is required')

        base_date = _parse_date(body.get('base_date'), 'base_date')
        if base_date is None:
            base_date = timezone.localdate()

        started_at = (
            _parse_effective_at(body.get('started_at'))
            if body.get('started_at') not in (None, '')
            else None
        )
        finished_at = (
            _parse_effective_at(body.get('finished_at'))
            if body.get('finished_at') not in (None, '')
            else None
        )
        if started_at is None or finished_at is None:
            raise StockValidationError('started_at and finished_at are required')

        staff_count = body.get('staff_count')
        if staff_count not in (None, ''):
            staff_count = int(staff_count)
            if staff_count < 0:
                raise StockValidationError('staff_count must be >= 0')
        else:
            staff_count = None

        unit_id = body.get('unit_id')
        entry, run = services.record_downtime(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=int(location_id),
            resource_id=int(resource_id),
            base_date=base_date,
            unit_id=int(unit_id) if unit_id not in (None, '') else None,
            counterparty_location_id=(
                int(body['counterparty_location_id'])
                if body.get('counterparty_location_id') not in (None, '')
                else None
            ),
            shift_code=(
                str(body['shift_code'])
                if body.get('shift_code') not in (None, '')
                else None
            ),
            staff_count=staff_count,
            started_at=started_at,
            finished_at=finished_at,
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Downtime recorded.',
        {'entry': entry_dict(entry), 'run': production_run_dict(run)},
        status_code=201,
    )


@csrf_exempt
@require_GET
def production_requirements_api(request, entry_id: int):
    """Recipe explode + optional on-hand piles for floor allocate."""
    location_id = request.GET.get('location_id')
    try:
        loc = int(location_id) if location_id not in (None, '') else None
        data = services.production_requirements(
            output_entry_id=entry_id,
            location_id=loc,
        )
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Production requirements fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
def production_consume_api(request, entry_id: int):
    """Floor allocate: stock-out a component pile against production output."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.production_consume(
            idempotency_key=body['idempotency_key'],
            output_entry_id=entry_id,
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Production consume posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def receipt_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.receipt(
            idempotency_key=body['idempotency_key'],
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            unit_cost=(
                _parse_decimal(body['unit_cost'], 'unit_cost')
                if body.get('unit_cost') not in (None, '')
                else None
            ),
            **_common_write_kwargs(request, body),
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
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
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
            lot=_resolve_lot(body),
            from_location_id=int(body['from_location_id']),
            to_location_id=int(body['to_location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
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
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
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
        counted = body.get('counted_quantity')
        delta = body.get('quantity_delta')
        entry = services.count_adjustment(
            idempotency_key=body['idempotency_key'],
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            counted_quantity=(
                _parse_decimal(counted, 'counted_quantity')
                if counted not in (None, '')
                else None
            ),
            quantity_delta=(
                _parse_decimal(delta, 'quantity_delta')
                if delta not in (None, '')
                else None
            ),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
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
            **_common_write_kwargs(request, body),
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
def audit_timeline_api(request):
    """Stock audit timeline: who/what/when from immutable stock_entry rows."""
    qs = (
        StockEntry.objects.select_related(
            'unit',
            'location',
            'counterparty_location',
            'lot__product',
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

    data = [serialize_balance_row(b) for b in qs[:500]]
    return api_success('Balances fetched.', data)


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