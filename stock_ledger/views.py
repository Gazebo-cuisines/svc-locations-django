import base64
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.models import (
    Product,
    ProductGoodsInType,
    ProductSupplier,
    PurchaseShapeFormat,
    Unit,
)
from product.query import active_products
from recipe.models import RecipeVersion
from stock_ledger.models import (
    ProductionRun,
    StockBalance,
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockReportEmailRecipient,
    StockReservation,
    StockUnit,
    StockUnitConversion,
    StockFifoOverride,
)
from stock_ledger.stream import iter_sse, subscribe
from stock_ledger.util import (
    entry_labels,
    entry_posting,
    manage,
    reservations,
    scan,
    services,
    stickers,
    stock_units,
)
from stock_ledger.util.allocation_status import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NO_RECIPE,
    allocation_status,
    allocation_status_for_entries,
    exclude_incomplete_lot_ids,
    held_balance_keys,
)
from stock_ledger.util.conversions import (
    StockValidationError,
    stock_to_kg,
    stock_to_packs,
)
from stock_ledger.util.manage import ManageRemoveError
from stock_ledger.util.fifo import FIFO_ORDER, fifo_balances
from stock_ledger.util.product_supplier_lookup import (
    product_supplier_for_entry,
    product_supplier_for_lot,
)
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    receipt_meta_by_lot_ids,
    serialize_balance_row,
    supplier_pack_fields,
)
from stock_ledger.util.recall import (
    RecallLookupError,
    build_product_genealogy_index,
    build_recall_report,
)
from stock_ledger.util.reports import (
    closing_balances_as_of,
    consolidate_closing_balances,
    goods_out_movements_report,
    movements_report,
    operator_activity_detail,
    operator_activity_report,
)
from stock_ledger.util.closing_stock_email import (
    recipient_id_from_unsubscribe_token,
)
from stock_ledger.util.trace import (
    mass_balance_for_output,
    trace_backward,
    trace_forward,
)
from hardware.services import codes_for_serials, serial_from_request, touch_from_request
from users_rbac.auth import attach_user, client_ip
from users_rbac.permissions import (
    gate_floor_write,
    gate_production_write,
    gate_stock_management,
    gate_warehouse_write,
    require_any_admin,
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


def _parse_clock(value, field_name: str):
    """HH:MM or HH:MM:SS → time; empty → None."""
    if value in (None, ''):
        return None
    text = str(value).strip()
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f'Invalid time for {field_name}. Use HH:MM.')


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
        'product_supplier_id': lot.product_supplier_id,
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
    mapping = product_supplier_for_entry(entry)
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
        if mapping.multiplier != 0 and product is not None:
            try:
                pack_quantity = _dec(
                    stock_to_packs(abs(entry.quantity), mapping, product),
                )
            except StockValidationError:
                pack_quantity = None
    display_kg = (
        _dec(stock_to_kg(abs(entry.quantity), product))
        if product is not None else None
    )
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
        'display_kg': display_kg,
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
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'reverses_entry_id': entry.reverses_entry_id,
        'source_entry_id': entry.source_entry_id,
        'source_entry_code': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id
            else None
        ),
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
        'device_serial': entry.device_serial,
        'device_code': codes_for_serials([entry.device_serial]).get(entry.device_serial),
        'remarks': entry.remarks,
        'entry_hash': entry.entry_hash,
        'prev_hash': entry.prev_hash,
    }


def audit_event_dict(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product
    posting = entry_posting.get_posting(entry)
    row = {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'action': entry.entry_type,
        'quantity': _dec(entry.quantity),
        # Base unit (KG) amount: the only figure comparable across mixed
        # entry units (grams, Kg, Box) on the same product.
        'quantity_base': _dec(entry.quantity_base),
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
        'device_serial': entry.device_serial,
        'device_code': codes_for_serials([entry.device_serial]).get(entry.device_serial),
        'posting_status': posting.status if posting is not None else None,
    }
    label = entry_labels.get_label(entry)
    if label is not None:
        row['label_status'] = label.status
    return row


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
        lot = StockLot.objects.select_related(
            'product',
            'product__destination_container',
        ).get(pk=lot_id)
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
        product_supplier_id=body.get('product_supplier_id') or None,
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

    device_serial = serial_from_request(request, body)
    if device_serial:
        touch_from_request(
            request,
            action='heartbeat',
            body=body,
            user=user,
            record_event=False,
        )

    kwargs = {
        'override_reason': body.get('override_reason'),
        'authorised_by_user_id': body.get('authorised_by_user_id'),
        'actor_user_id': actor_user_id,
        'lan_username': lan_username,
        'source_workstation': source_workstation,
        'source_workstation_ip': source_workstation_ip,
        'device_serial': device_serial,
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
        queue_stock = body.get('queue_stock') in (True, 'true', '1', 'yes', 'True')
        label_format = body.get('label_format')
        label_count = 1
        if label_format not in (None, ''):
            label_format = str(label_format).strip().lower()
            if label_format not in ('pallet', 'box'):
                raise StockValidationError('label_format must be pallet or box.')
            if body.get('label_count') in (None, ''):
                label_count = 1 if label_format == 'pallet' else 0
            else:
                label_count = int(body['label_count'])
            if label_count < 1:
                raise StockValidationError(
                    'label_count is required when label_format is set.',
                )
            # pallet: label_count = print copies of one barcode (not N receipts).
            if body.get('queue_stock') not in (
                False, 'false', '0', 'no', 'False',
            ):
                queue_stock = True

        total_qty = _parse_decimal(body['quantity'], 'quantity')
        split_parts = (
            1 if label_format == 'pallet' else label_count
        ) if label_format not in (None, '') else 1
        if split_parts == 1:
            qty_parts = [total_qty]
            unit_keys = [body['idempotency_key']]
        else:
            base = (total_qty / split_parts).quantize(Decimal('0.000001'))
            qty_parts = [base] * (split_parts - 1)
            qty_parts.append(
                (total_qty - sum(qty_parts)).quantize(Decimal('0.000001')),
            )
            if qty_parts[-1] <= 0:
                raise StockValidationError(
                    f'Cannot split quantity {total_qty} into {split_parts}.',
                )
            unit_keys = [
                f"{body['idempotency_key']}:u:{i}"
                for i in range(1, split_parts + 1)
            ]

        transactions = []
        data = None
        for unit_index, (unit_key, part_qty) in enumerate(
            zip(unit_keys, qty_parts), start=1,
        ):
            entry = services.receipt(
                idempotency_key=unit_key,
                lot=lot,
                location_id=int(body['location_id']),
                quantity=part_qty,
                unit_id=_optional_unit_id(body),
                effective_at=_parse_effective_at(body.get('effective_at')),
                unit_cost=(
                    _parse_decimal(body['unit_cost'], 'unit_cost')
                    if body.get('unit_cost') not in (None, '')
                    else None
                ),
                product_supplier=mapping,
                counterparty_location_id=supplier_location_id,
                defer_balance=queue_stock,
                **audit,
            )
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
            tx = entry_dict(entry)
            tx['unit_index'] = unit_index
            tx['entry_code'] = entry_labels.entry_code(entry.id)
            if queue_stock:
                posting = entry_posting.queue_entry(
                    entry=entry,
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['posting'] = entry_posting.posting_dict(posting)
                tx['posting_status'] = posting.status
            if label_format not in (None, ''):
                label = entry_labels.create_entry_label(
                    entry=entry,
                    label_format=label_format,
                    label_count=(
                        label_count if label_format == 'pallet' else 1
                    ),
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['label'] = entry_labels.label_state_dict(label)
                tx['goods_in_label'] = entry_labels.build_goods_in_label(
                    entry, label,
                )
            if unit_index == 1:
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
                            else f"{unit_key}:print"
                        ),
                        actor_user_id=audit.get('actor_user_id'),
                        lan_username=audit.get('lan_username'),
                        source_workstation=audit.get('source_workstation'),
                    )
                    tx['units'] = [stock_unit_dict(u) for u in units]
                data = dict(tx)
            transactions.append(tx)

        data['label_format'] = label_format
        data['label_count'] = label_count
        data['transaction_count'] = len(transactions)
        data['transactions'] = transactions
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Receipt posted.', data, status_code=201)

def _resolve_source_entry(body: dict) -> StockEntry | None:
    """Goods-in sticker (E{id}) that a goods-out row draws its stock from."""
    raw = body.get('source_entry_id')
    if raw in (None, '') and body.get('source_entry_code') not in (None, ''):
        raw = entry_labels.parse_entry_code(str(body['source_entry_code']))
        if raw is None:
            raise StockValidationError('source_entry_code must look like E123.')
    if raw in (None, ''):
        return None
    entry = entry_labels.get_entry_for_label(int(raw))
    entry_labels.require_receipt_entry(entry)
    return entry


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_out')
def issue_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source_entry = _resolve_source_entry(body)
        if source_entry is not None:
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


def _parse_requirement_ids(raw) -> list[int] | None:
    if raw in (None, ''):
        return None
    if not isinstance(raw, list):
        raise StockValidationError('requirement_ids must be a list.')
    if not raw:
        return None
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError) as exc:
        raise StockValidationError('requirement_ids must be integers.') from exc


def _product_destination_fields(product: Product) -> dict:
    """Auto dest for Goods Out without plan — product.destination_container."""
    dest = getattr(product, 'destination_container', None)
    dest_id = product.destination_container_id
    if dest is None and dest_id:
        dest = Location.objects.filter(pk=dest_id).only('id', 'name').first()
    return {
        'destination_container_id': dest_id,
        'destination_container_name': dest.name if dest is not None else None,
        'to_location_id': dest_id,
        'to_location_name': dest.name if dest is not None else None,
        'dest_ok': dest_id is not None,
    }


def _resolve_transfer_to_location_id(body: dict, lot: StockLot) -> int:
    """
    With plan / explicit body: to_location_id required.
    Without plan: omit to_location_id → product.destination_container.
    """
    raw = body.get('to_location_id')
    if raw not in (None, ''):
        return int(raw)
    dest_id = lot.product.destination_container_id
    if dest_id is None:
        raise StockValidationError(
            'This product has no destination location. '
            'Set destination_container on the product before Goods Out without plan.',
        )
    return int(dest_id)


def _transfer_label_plan(body: dict) -> tuple[str | None, int]:
    """Optional label_format/label_count for queued Goods OUT (mirror receipt)."""
    label_format = body.get('label_format')
    if label_format in (None, ''):
        return None, 1
    label_format = str(label_format).strip().lower()
    if label_format not in ('pallet', 'box'):
        raise StockValidationError('label_format must be pallet or box.')
    if body.get('label_count') in (None, ''):
        label_count = 1 if label_format == 'pallet' else 0
    else:
        label_count = int(body['label_count'])
    if label_count < 1:
        raise StockValidationError(
            'label_count is required when label_format is set.',
        )
    # pallet: label_count = print copies (transfer still one OUT row).
    return label_format, label_count


def _split_label_quantities(total: Decimal, parts: int) -> list[Decimal]:
    if parts < 1:
        raise StockValidationError('label_count must be >= 1.')
    if parts == 1:
        return [total]
    base = (total / parts).quantize(Decimal('0.000001'))
    qty_parts = [base] * (parts - 1)
    last = (total - sum(qty_parts)).quantize(Decimal('0.000001'))
    if last <= 0:
        raise StockValidationError(
            f'Cannot split quantity {total} into {parts}.',
        )
    qty_parts.append(last)
    return qty_parts


def _parse_transfer_lines(body: dict) -> list[dict] | None:
    """Multi-GI cart: [{lot_id, quantity, source_entry_id}, …]."""
    raw = body.get('lines')
    if raw in (None, ''):
        return None
    if not isinstance(raw, list) or not raw:
        raise StockValidationError('lines must be a non-empty array.')
    lines = []
    seen_entries: set[int] = set()
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            raise StockValidationError(f'lines[{i}] must be an object.')
        lot = _resolve_lot(row)
        qty = _parse_decimal(row.get('quantity'), f'lines[{i}].quantity')
        if qty <= 0:
            raise StockValidationError(f'lines[{i}].quantity must be > 0.')
        source_entry = _resolve_source_entry(row)
        if source_entry is None:
            raise StockValidationError(
                f'lines[{i}].source_entry_id is required.',
            )
        if source_entry.id in seen_entries:
            raise StockValidationError(
                f'Duplicate source_entry_id {source_entry.id} in lines.',
            )
        seen_entries.add(source_entry.id)
        lines.append({
            'lot': lot,
            'quantity': qty,
            'source_entry': source_entry,
        })
    product_ids = {line['lot'].product_id for line in lines}
    if len(product_ids) != 1:
        raise StockValidationError(
            'All lines must be for the same product.',
        )
    return lines


def _fifo_override_needed_for_work(
    *,
    product_id: int,
    location_id: int,
    work: list[dict],
) -> tuple[bool, int | None]:
    """Simulate draws in order; True if any line skips current oldest lot."""
    batches = _fifo_batch_rows(product_id=product_id, location_id=location_id)
    if not batches:
        return False, None
    remaining = {
        row['lot_id']: Decimal(str(row['quantity'])) for row in batches
    }
    recommended = batches[0]['lot_id']
    needs = False
    for item in work:
        lot_id = item['lot'].id
        oldest = next(
            (
                row['lot_id']
                for row in batches
                if remaining.get(row['lot_id'], Decimal('0')) > 0
            ),
            None,
        )
        if oldest is not None and lot_id != oldest:
            needs = True
        remaining[lot_id] = remaining.get(lot_id, Decimal('0')) - item['quantity']
    return needs, recommended


def _suggest_goods_out_picks(
    *,
    product: Product,
    location_id: int,
    required: Decimal,
) -> dict:
    """FIFO GI stickers covering required qty (full boxes; may overshoot)."""
    if required <= 0:
        raise StockValidationError('quantity must be > 0.')
    batches = _fifo_batch_rows(product_id=product.id, location_id=location_id)
    need = required
    picks = []
    suggested = Decimal('0')
    for rank, row in enumerate(batches):
        if need <= 0:
            break
        lot_id = row['lot_id']
        lot_left = Decimal(str(row['quantity']))
        if lot_left <= 0:
            continue
        receipts = list(
            StockEntry.objects
            .filter(
                entry_type=StockEntryType.RECEIPT,
                lot_id=lot_id,
                location_id=location_id,
                reversed_by__isnull=True,
            )
            .select_related(
                'lot__product',
                'lot__product_supplier__outer_unit',
                'unit',
            )
            .order_by('id')
        )
        for receipt in receipts:
            if need <= 0 or lot_left <= 0:
                break
            rem = stickers.remaining_for_entry(receipt, lot_quantity=lot_left)
            if rem <= 0:
                continue
            lot = receipt.lot
            pack = supplier_pack_fields(
                rem, product, getattr(lot, 'product_supplier', None),
            )
            picks.append({
                'entry_id': receipt.id,
                'entry_code': entry_labels.entry_code(receipt.id),
                'lot_id': lot.id,
                'trace_number': lot.trace_number,
                'use_by': lot.use_by.isoformat() if lot.use_by else None,
                'quantity': _dec(rem),
                'fifo_rank': rank,
                **pack,
            })
            suggested += rem
            lot_left -= rem
            need -= rem
    shortfall = need if need > 0 else Decimal('0')
    dest = _product_destination_fields(product)
    return {
        'product_id': product.id,
        'product_name': product.name,
        'location_id': location_id,
        'required_quantity': _dec(required),
        'suggested_quantity': _dec(suggested),
        'shortfall': _dec(shortfall),
        'display_kg': _dec(stock_to_kg(suggested, product)),
        'pick_count': len(picks),
        'picks': picks,
        **{k: dest[k] for k in (
            'to_location_id', 'to_location_name', 'dest_ok',
            'destination_container_id', 'destination_container_name',
        )},
    }


@csrf_exempt
@require_GET
def goods_out_suggest_api(request):
    """Qty-first FIFO sticker list for Goods Out without plan."""
    product_id = request.GET.get('product_id')
    location_id = request.GET.get('location_id')
    quantity = request.GET.get('quantity')
    if product_id in (None, ''):
        return api_error('product_id is required.')
    if location_id in (None, ''):
        return api_error('location_id is required.')
    if quantity in (None, ''):
        return api_error('quantity is required.')
    try:
        pid = int(product_id)
        loc_id = int(location_id)
        required = _parse_decimal(quantity, 'quantity')
    except (TypeError, ValueError) as exc:
        return api_error(str(exc))
    product = (
        Product.objects
        .select_related('destination_container', 'unit')
        .filter(pk=pid)
        .first()
    )
    if product is None:
        return api_error(f'product_id={pid} not found.', status_code=404)
    try:
        data = _suggest_goods_out_picks(
            product=product,
            location_id=loc_id,
            required=required,
        )
    except StockValidationError as exc:
        return api_error(str(exc))
    if not data['dest_ok']:
        data['dest_message'] = (
            'This product has no destination location. '
            'Fix product master before Goods Out without plan.'
        )
    touch_from_request(request, action='scan', location_id=loc_id)
    return api_success('Goods Out picks suggested.', data)


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
        queue_stock = body.get('queue_stock') in (True, 'true', '1', 'yes', 'True')
        label_format, label_count = _transfer_label_plan(body)
        if label_format is not None and body.get('queue_stock') not in (
            False, 'false', '0', 'no', 'False',
        ):
            queue_stock = True
        lines = _parse_transfer_lines(body)
        split_labels = label_format == 'box' and label_count > 1
        if lines is not None and split_labels:
            raise StockValidationError(
                'lines cannot be combined with label_count > 1.',
            )
        if lines is not None and unit_moves is not None:
            raise StockValidationError(
                'unit_moves cannot be combined with lines.',
            )
        if split_labels and unit_moves is not None:
            raise StockValidationError(
                'unit_moves cannot be combined with label_count > 1.',
            )
        audit = _common_write_kwargs(request, body)
        req_ids = _parse_requirement_ids(body.get('requirement_ids'))
        auto_dest = body.get('to_location_id') in (None, '')
        if req_ids:
            audit['source_document_type'] = 'plan_requirement'
            audit['source_document_id'] = req_ids[0]
        elif auto_dest and not audit.get('source_document_type'):
            # Without-plan goods out — dest from product; never invent a plan line.
            audit['source_document_type'] = 'goods_out_adhoc'
        from_location_id = int(body['from_location_id'])
        fifo_reason = str(body.get('fifo_override_reason') or '').strip()
        effective_at = _parse_effective_at(body.get('effective_at'))
        unit_id = _optional_unit_id(body)
        out_fmt = label_format or 'box'
        required_quantity = None
        if body.get('required_quantity') not in (None, ''):
            required_quantity = _parse_decimal(
                body['required_quantity'], 'required_quantity',
            )

        if lines is not None:
            work = []
            for i, line in enumerate(lines, start=1):
                lot = line['lot']
                source_entry = line['source_entry']
                part_qty = line['quantity']
                balance = (
                    StockBalance.objects
                    .filter(lot_id=lot.id, location_id=from_location_id)
                    .only('quantity')
                    .first()
                )
                stickers.check_draw(
                    source_entry=source_entry,
                    lot=lot,
                    location_id=from_location_id,
                    quantity=part_qty,
                    lot_quantity=(
                        balance.quantity if balance is not None else None
                    ),
                )
                work.append({
                    'key': (
                        body['idempotency_key']
                        if len(lines) == 1
                        else f"{body['idempotency_key']}:l:{i}"
                    ),
                    'lot': lot,
                    'quantity': part_qty,
                    'source_entry': source_entry,
                    'unit_moves': None,
                })
            if required_quantity is not None:
                picked = sum((item['quantity'] for item in work), Decimal('0'))
                if picked < required_quantity:
                    raise StockValidationError(
                        f'Picked {picked} is less than required '
                        f'{required_quantity}.',
                    )
            first_lot = work[0]['lot']
            to_location_id = _resolve_transfer_to_location_id(body, first_lot)
            needs_override, recommended_lot_id = _fifo_override_needed_for_work(
                product_id=first_lot.product_id,
                location_id=from_location_id,
                work=work,
            )
            if queue_stock and needs_override and not fifo_reason:
                raise StockValidationError(
                    'fifo_override_reason is required when not using oldest stock.',
                )
            response_label_count = len(work)
        else:
            lot = _resolve_lot(body)
            to_location_id = _resolve_transfer_to_location_id(body, lot)
            total_qty = _parse_decimal(body['quantity'], 'quantity')
            source_entry = _resolve_source_entry(body)
            if source_entry is not None:
                balance = (
                    StockBalance.objects
                    .filter(lot_id=lot.id, location_id=from_location_id)
                    .only('quantity')
                    .first()
                )
                stickers.check_draw(
                    source_entry=source_entry,
                    lot=lot,
                    location_id=from_location_id,
                    quantity=total_qty,
                    lot_quantity=(
                        balance.quantity if balance is not None else None
                    ),
                )
            qty_parts = _split_label_quantities(
                total_qty,
                1 if label_format == 'pallet' else label_count,
            )
            split_n = len(qty_parts)
            if split_n == 1:
                unit_keys = [body['idempotency_key']]
            else:
                unit_keys = [
                    f"{body['idempotency_key']}:u:{i}"
                    for i in range(1, split_n + 1)
                ]
            work = [
                {
                    'key': unit_key,
                    'lot': lot,
                    'quantity': part_qty,
                    'source_entry': source_entry,
                    'unit_moves': unit_moves if split_n == 1 else None,
                }
                for unit_key, part_qty in zip(unit_keys, qty_parts)
            ]
            recommended_lot_id = None
            if queue_stock:
                batches = _fifo_batch_rows(
                    product_id=lot.product_id,
                    location_id=from_location_id,
                )
                recommended_lot_id = batches[0]['lot_id'] if batches else None
                if (
                    recommended_lot_id is not None
                    and recommended_lot_id != lot.id
                    and not fifo_reason
                ):
                    raise StockValidationError(
                        'fifo_override_reason is required when not using '
                        'oldest stock.',
                    )
            response_label_count = label_count

        transactions = []
        for unit_index, item in enumerate(work, start=1):
            lot = item['lot']
            out_entry, in_entry = services.transfer(
                idempotency_key=item['key'],
                lot=lot,
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                quantity=item['quantity'],
                unit_id=unit_id,
                effective_at=effective_at,
                unit_moves=item['unit_moves'],
                defer_balance=queue_stock,
                source_entry=item['source_entry'],
                **audit,
            )
            tx = {
                'unit_index': unit_index,
                'out': entry_dict(out_entry),
                'in': entry_dict(in_entry),
            }
            if queue_stock:
                out_entry = (
                    StockEntry.objects
                    .select_related('lot__product', 'unit')
                    .get(pk=out_entry.pk)
                )
                posting = entry_posting.queue_entry(
                    entry=out_entry,
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                label = entry_labels.create_entry_label(
                    entry=out_entry,
                    label_format=out_fmt,
                    label_count=(
                        label_count if out_fmt == 'pallet' else 1
                    ),
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['out'] = entry_dict(out_entry)
                tx['posting'] = entry_posting.posting_dict(posting)
                tx['label'] = entry_labels.label_state_dict(label)
                tx['goods_out_label'] = entry_labels.build_goods_out_label(
                    issue_entry=out_entry,
                    copies=label.label_count,
                )
                if (
                    recommended_lot_id is not None
                    and recommended_lot_id != lot.id
                    and fifo_reason
                ):
                    StockFifoOverride.objects.get_or_create(
                        stock_entry=out_entry,
                        defaults={
                            'product_id': lot.product_id,
                            'scanned_lot_id': lot.id,
                            'recommended_lot_id': recommended_lot_id,
                            'reason': fifo_reason,
                            'actor_user_id': audit.get('actor_user_id'),
                            'lan_username': (audit.get('lan_username') or None),
                        },
                    )
            transactions.append(tx)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))

    first = transactions[0]
    payload = {
        'out': first['out'],
        'in': first['in'],
        'label_format': out_fmt if queue_stock else label_format,
        'label_count': response_label_count,
        'transaction_count': len(transactions),
        'transactions': transactions,
    }
    if required_quantity is not None:
        payload['required_quantity'] = _dec(required_quantity)
    if queue_stock:
        payload['posting'] = first['posting']
        payload['label'] = first['label']
        payload['goods_out_label'] = first['goods_out_label']
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
        'Transfer queued.' if queue_stock else 'Transfer posted.',
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
        source_entry = _resolve_source_entry(body)
        lot = _resolve_lot(body) if source_entry is None else source_entry.lot
        location_id = (
            source_entry.location_id
            if source_entry is not None and body.get('location_id') in (None, '')
            else int(body['location_id'])
        )
        entry = services.count_adjustment(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=location_id,
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
            source_entry=source_entry,
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
    """Stock audit timeline: all stock_entry rows, newest recorded_at first."""
    qs = (
        StockEntry.objects.select_related(
            'unit',
            'location',
            'counterparty_location',
            'lot__product',
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
    rows = [audit_event_dict(entry) for entry in qs[row_offset : row_offset + row_limit]]
    return api_success(
        'Stock audit timeline fetched.',
        {
            'items': rows,
            'count': count,
            'limit': row_limit,
            'offset': row_offset,
            'has_more': row_offset + len(rows) < count,
            'order': 'recorded_at_desc',
        },
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
