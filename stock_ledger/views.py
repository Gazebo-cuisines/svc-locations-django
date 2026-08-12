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

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.models import Product, ProductSupplier, PurchaseShapeFormat, Unit
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
    StockUnit,
    StockUnitConversion,
)
from stock_ledger.stream import iter_sse, subscribe
from stock_ledger.util import entry_labels, reservations, scan, services, stock_units
from stock_ledger.util.allocation_status import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NO_RECIPE,
    allocation_status,
    allocation_status_for_entries,
    exclude_incomplete_lot_ids,
    held_balance_keys,
)
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.fifo import FIFO_ORDER, fifo_balances
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    receipt_meta_by_lot_ids,
    serialize_balance_row,
)
from stock_ledger.util.trace import (
    mass_balance_for_output,
    trace_backward,
    trace_forward,
)
from users_rbac.auth import attach_user, client_ip
from users_rbac.permissions import (
    gate_floor_write,
    gate_production_write,
    gate_warehouse_write,
)


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _dec(value):
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


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


def stock_unit_dict(unit: StockUnit) -> dict:
    return {
        'id': unit.id,
        'unit_serial': unit.unit_serial,
        'lot_id': unit.lot_id,
        'location_id': unit.location_id,
        'unit_id': unit.unit_id,
        'quantity_initial': _dec(unit.quantity_initial),
        'quantity_remaining': _dec(unit.quantity_remaining),
        'status': unit.status,
        'created_by_entry_id': unit.created_by_entry_id,
        'created_at': unit.created_at.isoformat() if unit.created_at else None,
        'voided_at': unit.voided_at.isoformat() if unit.voided_at else None,
        'void_reason': unit.void_reason,
        'gs1': stock_units.build_gs1_payload(unit),
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
    counterparty = entry.counterparty_location
    location = entry.location
    lot = entry.lot
    product = lot.product if lot is not None else None
    unit = entry.unit
    # transfer_out / issue: location → counterparty; transfer_in: counterparty → location;
    # receipt: supplier (counterparty) → location
    if entry.entry_type == StockEntryType.TRANSFER_IN:
        from_loc, to_loc = counterparty, location
    elif entry.entry_type in (
        StockEntryType.TRANSFER_OUT,
        StockEntryType.ISSUE,
        StockEntryType.DISPOSAL,
    ):
        from_loc, to_loc = location, counterparty
    else:
        from_loc, to_loc = counterparty, location
    mapping = _product_supplier_for_entry(entry)
    pack_quantity = None
    pack_unit_name = None
    shape_format_label = None
    shape_format_id = lot.shape_format_id if lot is not None else None
    shape_format_name = (
        lot.shape_format.name
        if lot is not None and lot.shape_format_id and getattr(lot, 'shape_format', None)
        else None
    )
    outer_qty = None
    outer_unit_name = None
    inner_qty = None
    inner_unit_name = None
    multiplier = None
    if mapping is not None:
        shape_format_label = mapping.shape_format_label
        if mapping.purchase_shape_format_id:
            shape_format_id = mapping.purchase_shape_format_id
            if mapping.purchase_shape_format is not None:
                shape_format_name = mapping.purchase_shape_format.name
        outer_qty = _dec(mapping.outer_qty)
        outer_unit_name = mapping.outer_unit.name if mapping.outer_unit_id else None
        inner_qty = _dec(mapping.inner_qty)
        inner_unit_name = mapping.inner_unit.name if mapping.inner_unit_id else None
        multiplier = _dec(mapping.multiplier)
        pack_unit_name = outer_unit_name
        # New receipts store stock kg with base_unit_factor = multiplier.
        if (
            entry.base_unit_factor is not None
            and entry.base_unit_factor == mapping.multiplier
            and mapping.multiplier != 0
        ):
            pack_quantity = _dec(entry.quantity / mapping.multiplier)
    return {
        'id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'idempotency_key': entry.idempotency_key,
        'entry_type': entry.entry_type,
        'lot_id': entry.lot_id,
        'trace_number': lot.trace_number if lot is not None else None,
        'supplier_lot_code': lot.supplier_lot_code if lot is not None else None,
        'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
        'production_date': (
            lot.production_date.isoformat()
            if lot is not None and lot.production_date
            else None
        ),
        'product_id': product.id if product is not None else None,
        'product_name': product.name if product is not None else None,
        'location_id': entry.location_id,
        'location_name': location.name if location is not None else None,
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            counterparty.name if counterparty is not None else None
        ),
        'from_location_id': from_loc.id if from_loc is not None else None,
        'from_location_name': from_loc.name if from_loc is not None else None,
        'to_location_id': to_loc.id if to_loc is not None else None,
        'to_location_name': to_loc.name if to_loc is not None else None,
        'supplier_id': entry.counterparty_location_id,
        'supplier_name': counterparty.name if counterparty is not None else None,
        'transfer_group_id': entry.transfer_group_id,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'unit_name': unit.name if unit is not None else None,
        'pack_quantity': pack_quantity,
        'pack_unit_name': pack_unit_name,
        'shape_format_id': shape_format_id,
        'shape_format_name': shape_format_name,
        'shape_format_label': shape_format_label,
        'shape_outer_qty': outer_qty,
        'shape_outer_unit_name': outer_unit_name,
        'shape_inner_qty': inner_qty,
        'shape_inner_unit_name': inner_unit_name,
        'shape_multiplier': multiplier,
        'product_supplier_id': mapping.id if mapping is not None else None,
        'base_unit_factor': _dec(entry.base_unit_factor),
        'quantity_base': _dec(entry.quantity_base),
        'unit_cost': _dec(entry.unit_cost),
        'line_cost': _dec(entry.line_cost),
        'period_id': entry.period_id,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'reverses_entry_id': entry.reverses_entry_id,
        'override_reason': entry.override_reason,
        'authorised_by_user_id': entry.authorised_by_user_id,
        'po_number': entry.po_number,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'source_document_line': entry.source_document_line,
        'actor_user_id': entry.actor_user_id,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
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


def _allocation_fields(status: dict | None) -> dict:
    if status is None:
        return {
            'allocation_status': STATUS_COMPLETE,
            'incomplete_reasons': [],
            'remaining_component_count': 0,
        }
    return {
        'allocation_status': status['allocation_status'],
        'incomplete_reasons': status['incomplete_reasons'],
        'remaining_component_count': status['remaining_component_count'],
    }


def production_list_row(
    run: ProductionRun,
    *,
    allocation: dict | None = None,
) -> dict:
    entry = run.stock_entry
    lot = entry.lot
    product = lot.product
    row = {
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
    row.update(_allocation_fields(allocation))
    return row


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


def _receipt_product_supplier(body: dict, lot: StockLot):
    """Resolve optional product_supplier mapping for goods-in shape/packs."""
    ps_id = body.get('product_supplier_id')
    if ps_id in (None, ''):
        return None
    try:
        row = (
            ProductSupplier.objects
            .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
            .get(pk=int(ps_id), is_active=True)
        )
    except (ProductSupplier.DoesNotExist, TypeError, ValueError) as exc:
        raise StockValidationError(
            f'product_supplier_id={ps_id} not found or inactive',
        ) from exc
    if row.product_id != lot.product_id:
        raise StockValidationError(
            f'product_supplier_id={ps_id} is for product_id={row.product_id}, '
            f'lot is product_id={lot.product_id}',
        )
    return row


def _receipt_supplier_location_id(body: dict, lot: StockLot) -> int | None:
    """Supplier comes from product_supplier (shape-format row) when given."""
    row = _receipt_product_supplier(body, lot)
    if row is not None:
        return row.supplier_id

    for key in ('counterparty_location_id', 'supplier_id'):
        raw = body.get(key)
        if raw not in (None, ''):
            return int(raw)
    return None


def _product_supplier_for_entry(entry: StockEntry):
    """Best-effort shape mapping for a receipt (product + supplier)."""
    if entry.entry_type != StockEntryType.RECEIPT:
        return None
    lot = entry.lot
    if lot is None or entry.counterparty_location_id is None:
        return None
    qs = (
        ProductSupplier.objects
        .filter(
            product_id=lot.product_id,
            supplier_id=entry.counterparty_location_id,
            is_active=True,
        )
        .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
    )
    if lot.shape_format_id:
        hit = qs.filter(purchase_shape_format_id=lot.shape_format_id).first()
        if hit is not None:
            return hit
    hit = qs.filter(is_default=True).first()
    return hit or qs.order_by('-id').first()


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
    attach_user(request, missing='ok', invalid='ok')
    user = getattr(request, 'rbac_user', None)
    if user:
        lan_username = (user.display_name or user.username)[:64]
        source_workstation = (request.META.get('HTTP_USER_AGENT') or '')[:64] or None
        source_workstation_ip = (
            getattr(request, 'client_ip', None) or client_ip(request) or ''
        )[:45] or None
        actor_user_id = user.id
    else:
        claims = _decode_bearer_claims(request)
        # Person-facing label (match product audit). Never fall back to Cognito sub.
        lan_username = body.get('lan_username') or (
            claims.get('name')
            or claims.get('email')
            or claims.get('cognito:username')
            or claims.get('username')
        )
        source_workstation = (
            body.get('source_workstation') or request.META.get('HTTP_USER_AGENT')
        )
        source_workstation_ip = (
            body.get('source_workstation_ip') or request.META.get('REMOTE_ADDR')
        )
        actor_user_id = body.get('actor_user_id')
        if lan_username is not None:
            lan_username = str(lan_username)[:64]
        if source_workstation is not None:
            source_workstation = str(source_workstation)[:64]
        if source_workstation_ip is not None:
            source_workstation_ip = str(source_workstation_ip)[:45]

    kwargs = {
        'override_reason': body.get('override_reason'),
        'authorised_by_user_id': body.get('authorised_by_user_id'),
        'actor_user_id': actor_user_id,
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


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def receipt_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        if (
            body.get('po_number') not in (None, '')
            or str(body.get('source_document_type') or '').lower() == 'po'
        ):
            return api_error(
                'PO goods-in must use POST /purchasing/pos/<po_id>/receive/. '
                'Do not pass po_number or source_document_type=po to /stock/receipt/.',
            )
        audit = _common_write_kwargs(request, body)
        lot = _resolve_lot(body)
        mapping = _receipt_product_supplier(body, lot)
        supplier_location_id = (
            mapping.supplier_id if mapping is not None
            else _receipt_supplier_location_id(body, lot)
        )
        if (
            lot.origin == StockLotOrigin.PURCHASE
            and supplier_location_id is None
        ):
            raise StockValidationError(
                'supplier_id or product_supplier_id is required for purchase goods-in.',
            )
        entry = services.receipt(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            unit_cost=(
                _parse_decimal(body['unit_cost'], 'unit_cost')
                if body.get('unit_cost') not in (None, '')
                else None
            ),
            product_supplier=mapping,
            counterparty_location_id=supplier_location_id,
            **audit,
        )
        # Ensure supplier name is available without extra query when FK loaded.
        if supplier_location_id is not None and entry.counterparty_location_id:
            entry = (
                StockEntry.objects
                .select_related(
                    'counterparty_location',
                    'location',
                    'unit',
                    'lot__product',
                )
                .get(pk=entry.pk)
            )
        data = entry_dict(entry)
        print_count = body.get('print_unit_count')
        print_qty = body.get('print_quantity_per_unit')
        if print_count not in (None, '') or print_qty not in (None, ''):
            if print_count in (None, '') or print_qty in (None, ''):
                return api_error(
                    'print_unit_count and print_quantity_per_unit are both required to print labels.',
                )
            units = stock_units.create_units_for_entry(
                source_entry=entry,
                unit_count=int(print_count),
                quantity_per_unit=_parse_decimal(
                    print_qty, 'print_quantity_per_unit',
                ),
                idempotency_key_prefix=(
                    str(body['print_idempotency_key_prefix'])
                    if body.get('print_idempotency_key_prefix') not in (None, '')
                    else f"{body['idempotency_key']}:print"
                ),
                actor_user_id=audit.get('actor_user_id'),
                lan_username=audit.get('lan_username'),
                source_workstation=audit.get('source_workstation'),
            )
            data['units'] = [stock_unit_dict(u) for u in units]
        if body.get('label_format') not in (None, ''):
            label = entry_labels.create_entry_label(
                entry=entry,
                label_format=body.get('label_format'),
                label_count=body.get('label_count'),
                actor_user_id=audit.get('actor_user_id'),
                lan_username=audit.get('lan_username'),
                source_workstation=audit.get('source_workstation'),
            )
            data['label'] = entry_labels.label_state_dict(label)
            data['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Receipt posted.', data, status_code=201)

@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_out')
def issue_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source_entry = None
        source_raw = body.get('source_entry_id')
        if source_raw in (None, '') and body.get('source_entry_code') not in (None, ''):
            source_raw = entry_labels.parse_entry_code(str(body['source_entry_code']))
            if source_raw is None:
                raise StockValidationError('source_entry_code must look like E123.')
        if source_raw not in (None, ''):
            source_entry = entry_labels.get_entry_for_label(int(source_raw))
            entry_labels.require_receipt_entry(source_entry)
            lot = source_entry.lot
            location_id = (
                int(body['location_id'])
                if body.get('location_id') not in (None, '')
                else source_entry.location_id
            )
        else:
            lot = _resolve_lot(body)
            location_id = int(body['location_id'])
        entry = services.issue(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=location_id,
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
        data = entry_dict(entry)
        if source_entry is not None:
            data['source_entry_id'] = source_entry.id
            data['source_entry_code'] = entry_labels.entry_code(source_entry.id)
        copies = body.get('goods_out_label_count')
        if copies not in (None, ''):
            data['goods_out_label'] = entry_labels.build_goods_out_label(
                issue_entry=entry,
                source_entry=source_entry,
                copies=int(copies),
            )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Issue posted.', data, status_code=201)

def _parse_unit_moves(body: dict) -> list | None:
    raw = body.get('unit_moves')
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise StockValidationError('unit_moves must be a list')
    return raw


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def transfer_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    unit_moves = None
    try:
        unit_moves = _parse_unit_moves(body)
        out_entry, in_entry = services.transfer(
            idempotency_key=body['idempotency_key'],
            lot=_resolve_lot(body),
            from_location_id=int(body['from_location_id']),
            to_location_id=int(body['to_location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            unit_moves=unit_moves,
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))

    payload = {'out': entry_dict(out_entry), 'in': entry_dict(in_entry)}
    if unit_moves is not None:
        serials = []
        for row in unit_moves:
            if isinstance(row, dict) and row.get('unit_serial'):
                serials.append(
                    stock_units.resolve_unit_serial(str(row['unit_serial'])),
                )
        units = (
            StockUnit.objects
            .filter(unit_serial__in=serials)
            .select_related('lot__product', 'unit', 'location')
            .order_by('id')
        )
        payload['units'] = [stock_unit_dict(u) for u in units]
    return api_success(
        'Transfer posted.',
        payload,
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_out')
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
@gate_warehouse_write()
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
@gate_floor_write
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
    related = (
        'lot__product',
        'lot__shape_format',
        'unit',
        'location',
        'counterparty_location',
        'label',
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
        result = entry_labels.verify_label(
            entry_id=entry_id,
            code=str(body['code']),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        return api_error(msg, status_code=404 if 'not found' in msg else 400)
    return api_success('Label verified.', result)

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
        bucket['products'].append({
            'product_id': row['lot__product_id'],
            'product_name': row['lot__product__name'],
            'unit_id': row['lot__product__unit_id'],
            'unit_name': row['lot__product__unit__name'],
            'remaining_qty': _dec(row['remaining_qty']),
            'lot_count': row['lot_count'],
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


def _fifo_batch_rows(
    *,
    product_id: int,
    location_id: int | None,
    include_incomplete: bool = False,
) -> list[dict]:
    rows = list(fifo_balances(product_id=product_id, location_id=location_id)[:200])
    held = exclude_incomplete_lot_ids(
        location_id=location_id,
        lot_ids={b.lot_id for b in rows},
        include_incomplete=include_incomplete,
    )
    if held:
        rows = [b for b in rows if b.lot_id not in held]
    receipt_meta = receipt_meta_by_lot_ids({b.lot_id for b in rows})
    today = timezone.localdate()
    batches = []
    for rank, balance in enumerate(rows):
        lot = balance.lot
        row = serialize_balance_row(
            balance, receipt_meta=receipt_meta.get(balance.lot_id),
        )
        row['supplier_lot_code'] = lot.supplier_lot_code
        row['origin'] = lot.origin
        row['days_left'] = (lot.use_by - today).days if lot.use_by else None
        row['fifo_rank'] = rank
        batches.append(row)
    return batches


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
    include_incomplete = str(request.GET.get('include_incomplete', '')).lower() in (
        '1', 'true', 'yes',
    )
    batches = _fifo_batch_rows(
        product_id=product.id,
        location_id=loc_id,
        include_incomplete=include_incomplete,
    )
    total = sum(Decimal(row['quantity']) for row in batches) if batches else Decimal('0')

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
    entry = match.get('entry')
    if entry is not None:
        data['entry'] = entry_dict(entry)
        label = entry_labels.get_label(entry)
        data['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
        if label is not None:
            data['label'] = entry_labels.label_state_dict(label)
    return api_success('Scan resolved.', data)

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
