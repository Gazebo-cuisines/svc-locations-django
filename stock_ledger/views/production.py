from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.query import active_products
from stock_ledger.models import ProductionRun, StockEntryType, StockLotOrigin
from stock_ledger.util import services
from stock_ledger.util.allocation_status import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NO_RECIPE,
    allocation_status,
    allocation_status_for_entries,
)
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.parse import (
    optional_int_param as _optional_int_param,
    optional_unit_id as _optional_unit_id,
    parse_date as _parse_date,
    parse_decimal as _parse_decimal,
    parse_effective_at as _parse_effective_at,
)
from stock_ledger.util.payloads import (
    allocation_fields as _allocation_fields,
    entry_dict,
    production_list_row,
    production_run_dict,
)
from stock_ledger.views._common import (
    _common_write_kwargs,
    _parse_json_body,
    _resolve_lot,
)
from users_rbac.permissions import gate_production_write


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

    status_filter = str(request.GET.get('allocation_status', 'all')).lower()
    if status_filter not in ('all', 'incomplete', 'complete'):
        return api_error(
            'Invalid allocation_status. Use incomplete, complete, or all.',
        )

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
            'stock_entry__lot__recipe_version',
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

    runs = list(qs[:500])
    statuses = allocation_status_for_entries(
        [run.stock_entry_id for run in runs],
    )

    rows = []
    for run in runs:
        status = statuses.get(run.stock_entry_id)
        alloc = status['allocation_status'] if status else STATUS_COMPLETE
        # no_recipe counts as complete for the Incomplete filter.
        is_incomplete = alloc == STATUS_INCOMPLETE
        if status_filter == 'incomplete' and not is_incomplete:
            continue
        if status_filter == 'complete' and is_incomplete:
            continue
        rows.append(production_list_row(run, allocation=status))

    return api_success('Production entries fetched.', rows)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@gate_production_write
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
        {
            'entry': entry_dict(entry),
            'run': production_run_dict(run),
            **_allocation_fields(allocation_status(output_entry_id=entry.id)),
        },
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
@gate_production_write
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
            **_allocation_fields(allocation_status(output_entry_id=entry.id)),
        },
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@gate_production_write
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
@require_GET
def production_allocation_status_api(request, entry_id: int):
    """Management drill-down: why a MADE row is incomplete + full BOM remaining."""
    location_id = request.GET.get('location_id')
    try:
        loc = int(location_id) if location_id not in (None, '') else None
        status = allocation_status(output_entry_id=entry_id)
    except (ValueError, StockValidationError, TypeError) as exc:
        msg = str(exc)
        code = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=code)

    data = {
        **status,
        'location_id': loc,
        'made_quantity': None,
        'product_id': None,
        'recipe_version_id': None,
        'process_loss': None,
        'components': [],
    }
    if status['allocation_status'] != STATUS_NO_RECIPE:
        try:
            req = services.production_requirements(
                output_entry_id=entry_id,
                location_id=loc,
            )
            data.update({
                'made_quantity': req['made_quantity'],
                'product_id': req['product_id'],
                'recipe_version_id': req['recipe_version_id'],
                'process_loss': req['process_loss'],
                'components': req['components'],
            })
        except StockValidationError:
            pass

    return api_success('Production allocation status fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_production_write
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
