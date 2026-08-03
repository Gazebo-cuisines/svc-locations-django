"""Production register HTTP handlers — all business logic lives here (no services.py)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import LocationStockProfile
from locations.utils.api_response import api_error, api_success
from planning.models import PlanLine, Resource
from product.models import Product, ProductShelfLife, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from stock_ledger.models import StockBalance, StockLot, StockLotOrigin
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util import services as stock_services
from stock_ledger.views import julian_trace_number

from production_register.models import (
    ProductionDowntime,
    ProductionRun,
    ProductionRunConsumption,
    ProductionRunStatus,
    ProductionStation,
)

Q6 = Decimal('0.000001')


def _dec(value) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), 'f')


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field_name}.') from exc


def _parse_date(value, field_name: str):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.') from exc


def _parse_dt(value, field_name: str):
    if value in (None, ''):
        return None
    dt = parse_datetime(str(value))
    if dt is None:
        raise ValueError(f'Invalid datetime for {field_name}. Use ISO-8601.')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _err(message: str, code: str, *, status_code: int = 400, **extra):
    return api_error(
        message,
        {'code': code, **extra},
        status_code=status_code,
    )


def station_dict(row: ProductionStation) -> dict:
    return {
        'id': row.id,
        'code': row.code,
        'name': row.name,
        'location_id': row.location_id,
        'default_output_location_id': row.default_output_location_id,
        'default_consume_location_id': row.default_consume_location_id,
        'is_active': row.is_active,
    }


def consumption_dict(row: ProductionRunConsumption) -> dict:
    return {
        'id': row.id,
        'run_id': row.run_id,
        'component_product_id': row.component_product_id,
        'lot_id': row.lot_id,
        'quantity': _dec(row.quantity),
        'unit_id': row.unit_id,
        'needed_qty': _dec(row.needed_qty),
        'stock_entry_id': row.stock_entry_id,
    }


def run_dict(run: ProductionRun) -> dict:
    recipe_source = 'plan_line' if run.plan_line_id else 'active_latest'
    version_number = None
    if hasattr(run, 'recipe_version') and run.recipe_version_id:
        version_number = run.recipe_version.version_number
    return {
        'id': run.id,
        'station_id': run.station_id,
        'station_code': run.station.code if hasattr(run, 'station') else None,
        'status': run.status,
        'product_id': run.product_id,
        'quantity_made': _dec(run.quantity_made),
        'unit_id': run.unit_id,
        'recipe_version_id': run.recipe_version_id,
        'recipe_version_number': version_number,
        'recipe_source': recipe_source,
        'plan_line_id': run.plan_line_id,
        'from_location_id': run.from_location_id,
        'to_location_id': run.to_location_id,
        'resource_id': run.resource_id,
        'shift': run.shift,
        'start_at': run.start_at.isoformat() if run.start_at else None,
        'end_at': run.end_at.isoformat() if run.end_at else None,
        'production_date': (
            run.production_date.isoformat() if run.production_date else None
        ),
        'use_by': run.use_by.isoformat() if run.use_by else None,
        'trace_number': run.trace_number,
        'staff_count': run.staff_count,
        'trays': run.trays,
        'batches': run.batches,
        'idempotency_key': run.idempotency_key,
        'output_stock_entry_id': run.output_stock_entry_id,
        'created_at': run.created_at.isoformat() if run.created_at else None,
        'posted_at': run.posted_at.isoformat() if run.posted_at else None,
        'voided_at': run.voided_at.isoformat() if run.voided_at else None,
    }


def downtime_dict(row: ProductionDowntime) -> dict:
    return {
        'id': row.id,
        'station_id': row.station_id,
        'start_at': row.start_at.isoformat() if row.start_at else None,
        'end_at': row.end_at.isoformat() if row.end_at else None,
        'resource_id': row.resource_id,
        'shift': row.shift,
        'remarks': row.remarks,
    }


def _get_station(code: str) -> ProductionStation | None:
    return ProductionStation.objects.filter(code=code).first()


def _get_run(run_id: int) -> ProductionRun | None:
    return (
        ProductionRun.objects
        .select_related(
            'station',
            'recipe_version',
            'product',
            'output_stock_entry',
        )
        .prefetch_related(
            'consumptions',
            'consumptions__lot',
            'consumptions__stock_entry',
        )
        .filter(pk=run_id)
        .first()
    )


def _resolve_recipe_version_id(
    product_id: int,
    plan_line_id: int | None,
) -> tuple[int, str]:
    """R-BOM-5: plan line pin else latest ACTIVE."""
    if plan_line_id is not None:
        try:
            line = PlanLine.objects.get(pk=plan_line_id)
        except PlanLine.DoesNotExist as exc:
            raise ValueError('PLAN_LINE_MISSING') from exc
        if line.recipe_version_id is None:
            raise ValueError('PLAN_LINE_RECIPE_MISSING')
        if line.product_id != product_id:
            raise ValueError('PLAN_LINE_PRODUCT_MISMATCH')
        return line.recipe_version_id, 'plan_line'

    try:
        recipe = Recipe.objects.get(product_id=product_id)
    except Recipe.DoesNotExist as exc:
        raise ValueError('RECIPE_MISSING') from exc
    active = (
        RecipeVersion.objects
        .filter(recipe_id=recipe.id, status=RecipeVersionStatus.ACTIVE)
        .order_by('-version_number')
        .first()
    )
    if active is None:
        raise ValueError('RECIPE_MISSING')
    return active.id, 'active_latest'


def _auto_dates(
    *,
    product_id: int,
    to_location_id: int,
    production_date: date | None,
    use_by: date | None,
) -> tuple[date, date | None]:
    shelf = ProductShelfLife.objects.filter(product_id=product_id).first()
    profile = LocationStockProfile.objects.filter(location_id=to_location_id).first()

    if production_date is None:
        if shelf and shelf.force_production_date:
            raise ValueError('FORCE_PRODUCTION_DATE_REQUIRED')
        production_date = timezone.localdate()

    if use_by is None:
        if shelf and shelf.force_use_by:
            raise ValueError('FORCE_USE_BY_REQUIRED')
        days = shelf.shelf_life_days if shelf and shelf.shelf_life_days is not None else None
        if days is not None:
            modifier = 0
            if profile and profile.use_by_modifier is not None:
                modifier = int(profile.use_by_modifier)
            use_by = production_date + timedelta(days=int(days) + modifier)

    return production_date, use_by


def _needed_qty(component: RecipeComponent, run: ProductionRun) -> Decimal:
    """
    Floor scale: component.quantity * (made / batch_quantity).
    If batch_quantity unset → treat as per-1 finished unit.
    Parent process_loss > 0 scales inputs up: made / process_loss.
    """
    version = run.recipe_version
    made = Decimal(run.quantity_made)
    process_loss = version.process_loss or Decimal('1')
    if process_loss <= 0:
        process_loss = Decimal('1')
    gross_made = made / process_loss
    batch_qty = version.batch_quantity
    if batch_qty is not None and batch_qty > 0:
        scale = gross_made / batch_qty
    else:
        scale = gross_made
    return (component.quantity * scale).quantize(Q6, rounding=ROUND_HALF_UP)


def _component_map(recipe_version_id: int) -> dict[int, RecipeComponent]:
    rows = RecipeComponent.objects.filter(recipe_version_id=recipe_version_id)
    return {c.component_product_id: c for c in rows}


def _lots_for_product(product_id: int, location_id: int) -> list[dict]:
    qs = (
        StockBalance.objects
        .filter(
            lot__product_id=product_id,
            location_id=location_id,
            quantity__gt=0,
        )
        .select_related('lot')
        .order_by('lot__use_by', 'lot_id')
    )
    out = []
    for bal in qs:
        lot = bal.lot
        out.append({
            'lot_id': lot.id,
            'quantity_on_hand': _dec(bal.quantity),
            'use_by': lot.use_by.isoformat() if lot.use_by else None,
            'production_date': (
                lot.production_date.isoformat() if lot.production_date else None
            ),
            'trace_number': lot.trace_number,
            'location_id': location_id,
        })
    return out


def _preview_ingredients(run: ProductionRun) -> list[dict]:
    components = (
        RecipeComponent.objects
        .filter(recipe_version_id=run.recipe_version_id)
        .select_related('component_product')
        .order_by('line_no')
    )
    consume_location_id = run.station.default_consume_location_id
    ingredients = []
    for c in components:
        ingredients.append({
            'component_product_id': c.component_product_id,
            'component_name': c.component_product.name,
            'line_no': c.line_no,
            'needed_qty': _dec(_needed_qty(c, run)),
            'unit_id': c.unit_id,
            'lots': _lots_for_product(c.component_product_id, consume_location_id),
        })
    return ingredients


def _run_detail_payload(run: ProductionRun) -> dict:
    consumptions = [
        consumption_dict(c) for c in run.consumptions.all().order_by('id')
    ]
    return {'run': run_dict(run), 'consumptions': consumptions}


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------


@csrf_exempt
@require_GET
def stations_list_api(request):
    qs = ProductionStation.objects.all().order_by('code')
    if request.GET.get('include_inactive') not in ('1', 'true', 'True'):
        qs = qs.filter(is_active=True)
    return api_success('Stations fetched.', [station_dict(s) for s in qs])


@csrf_exempt
@require_GET
def station_detail_api(request, code: str):
    station = _get_station(code)
    if station is None:
        return _err('Station not found.', 'VALIDATION', status_code=404)
    return api_success('Station fetched.', station_dict(station))


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def runs_collection_api(request):
    if request.method == 'GET':
        qs = (
            ProductionRun.objects
            .select_related('station', 'recipe_version')
            .order_by('-id')
        )
        station_code = request.GET.get('station')
        if station_code:
            qs = qs.filter(station__code=station_code)
        status = request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        prod_date = request.GET.get('date')
        if prod_date:
            try:
                qs = qs.filter(production_date=_parse_date(prod_date, 'date'))
            except ValueError as exc:
                return _err(str(exc), 'VALIDATION')
        plan_line_id = request.GET.get('plan_line_id')
        if plan_line_id not in (None, ''):
            try:
                qs = qs.filter(plan_line_id=int(plan_line_id))
            except (TypeError, ValueError):
                return _err('plan_line_id must be an integer.', 'VALIDATION')
        return api_success('Runs fetched.', [run_dict(r) for r in qs[:200]])

    body = _parse_json_body(request)
    if body is None:
        return _err('Invalid JSON body.', 'VALIDATION')

    required = ['station_code', 'product_id', 'quantity_made', 'unit_id']
    for key in required:
        if body.get(key) in (None, ''):
            return _err(f'Missing required field: {key}', 'VALIDATION')

    station = _get_station(str(body['station_code']))
    if station is None or not station.is_active:
        return _err('Station not found or inactive.', 'VALIDATION', status_code=404)

    try:
        product_id = int(body['product_id'])
        unit_id = int(body['unit_id'])
        quantity_made = _parse_decimal(body['quantity_made'], 'quantity_made')
        if quantity_made <= 0:
            return _err('quantity_made must be positive.', 'VALIDATION')
        if not Product.objects.filter(pk=product_id).exists():
            return _err('product_id not found.', 'VALIDATION', status_code=404)
        if not Unit.objects.filter(pk=unit_id).exists():
            return _err('unit_id not found.', 'VALIDATION', status_code=404)

        plan_line_id = body.get('plan_line_id')
        plan_line_id = int(plan_line_id) if plan_line_id not in (None, '') else None

        recipe_version_id, _source = _resolve_recipe_version_id(product_id, plan_line_id)

        from_location_id = body.get('from_location_id') or station.location_id
        to_location_id = body.get('to_location_id') or station.default_output_location_id
        from_location_id = int(from_location_id)
        to_location_id = int(to_location_id)

        production_date = _parse_date(body.get('production_date'), 'production_date')
        use_by = _parse_date(body.get('use_by'), 'use_by')
        production_date, use_by = _auto_dates(
            product_id=product_id,
            to_location_id=to_location_id,
            production_date=production_date,
            use_by=use_by,
        )

        resource_id = body.get('resource_id')
        resource_id = int(resource_id) if resource_id not in (None, '') else None
        if resource_id is not None and not Resource.objects.filter(pk=resource_id).exists():
            return _err('resource_id not found.', 'VALIDATION', status_code=404)

        run = ProductionRun.objects.create(
            station=station,
            status=ProductionRunStatus.DRAFT,
            product_id=product_id,
            quantity_made=quantity_made,
            unit_id=unit_id,
            recipe_version_id=recipe_version_id,
            plan_line_id=plan_line_id,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            resource_id=resource_id,
            shift=body.get('shift') or None,
            start_at=_parse_dt(body.get('start_at'), 'start_at'),
            end_at=_parse_dt(body.get('end_at'), 'end_at'),
            production_date=production_date,
            use_by=use_by,
            trace_number=body.get('trace_number') or None,
            staff_count=body.get('staff_count'),
            trays=body.get('trays'),
            batches=body.get('batches'),
            actor_user_id=body.get('actor_user_id'),
        )
        run = _get_run(run.id)
        return api_success('Run created.', run_dict(run), status_code=201)
    except ValueError as exc:
        code = str(exc)
        messages = {
            'PLAN_LINE_MISSING': 'plan_line_id not found.',
            'PLAN_LINE_RECIPE_MISSING': 'Plan line has no recipe_version_id.',
            'PLAN_LINE_PRODUCT_MISMATCH': 'plan_line product does not match product_id.',
            'RECIPE_MISSING': 'No active recipe version for product.',
            'FORCE_USE_BY_REQUIRED': 'use_by is required for this product.',
            'FORCE_PRODUCTION_DATE_REQUIRED': 'production_date is required for this product.',
        }
        if code in messages:
            return _err(messages[code], code)
        return _err(str(exc), 'VALIDATION')


@csrf_exempt
@require_http_methods(['GET', 'PATCH'])
def run_detail_api(request, run_id: int):
    run = _get_run(run_id)
    if run is None:
        return _err('Run not found.', 'VALIDATION', status_code=404)

    if request.method == 'GET':
        return api_success('Run fetched.', _run_detail_payload(run))

    if run.status != ProductionRunStatus.DRAFT:
        return _err('Only draft runs can be updated.', 'NOT_DRAFT', status_code=409)

    body = _parse_json_body(request)
    if body is None:
        return _err('Invalid JSON body.', 'VALIDATION')

    try:
        clear_consumptions = False
        if 'quantity_made' in body and body['quantity_made'] not in (None, ''):
            qty = _parse_decimal(body['quantity_made'], 'quantity_made')
            if qty <= 0:
                return _err('quantity_made must be positive.', 'VALIDATION')
            if qty != run.quantity_made:
                clear_consumptions = True
            run.quantity_made = qty

        product_changed = False
        if 'product_id' in body and body['product_id'] not in (None, ''):
            new_product_id = int(body['product_id'])
            if new_product_id != run.product_id:
                product_changed = True
                clear_consumptions = True
            run.product_id = new_product_id

        plan_line_changed = False
        if 'plan_line_id' in body:
            raw = body['plan_line_id']
            new_plan_line_id = int(raw) if raw not in (None, '') else None
            if new_plan_line_id != run.plan_line_id:
                plan_line_changed = True
                clear_consumptions = True
            run.plan_line_id = new_plan_line_id

        if product_changed or plan_line_changed:
            recipe_version_id, _ = _resolve_recipe_version_id(
                run.product_id,
                run.plan_line_id,
            )
            run.recipe_version_id = recipe_version_id

        for field in ('shift', 'trace_number'):
            if field in body:
                setattr(run, field, body.get(field) or None)
        for field in ('staff_count', 'trays', 'batches', 'resource_id'):
            if field in body:
                val = body.get(field)
                setattr(run, field, int(val) if val not in (None, '') else None)
        for field in ('from_location_id', 'to_location_id', 'unit_id'):
            if field in body and body[field] not in (None, ''):
                setattr(run, field, int(body[field]))
        if 'start_at' in body:
            run.start_at = _parse_dt(body.get('start_at'), 'start_at')
        if 'end_at' in body:
            run.end_at = _parse_dt(body.get('end_at'), 'end_at')
        if 'production_date' in body or 'use_by' in body:
            pd = (
                _parse_date(body.get('production_date'), 'production_date')
                if 'production_date' in body
                else run.production_date
            )
            ub = (
                _parse_date(body.get('use_by'), 'use_by')
                if 'use_by' in body
                else run.use_by
            )
            if 'production_date' in body and body.get('production_date') in (None, ''):
                pd = None
            if 'use_by' in body and body.get('use_by') in (None, ''):
                ub = None
            pd, ub = _auto_dates(
                product_id=run.product_id,
                to_location_id=run.to_location_id,
                production_date=pd,
                use_by=ub,
            )
            run.production_date = pd
            run.use_by = ub

        with transaction.atomic():
            run.save()
            if clear_consumptions:
                run.consumptions.all().delete()

        run = _get_run(run.id)
        return api_success('Run updated.', _run_detail_payload(run))
    except ValueError as exc:
        code = str(exc)
        messages = {
            'PLAN_LINE_MISSING': 'plan_line_id not found.',
            'PLAN_LINE_RECIPE_MISSING': 'Plan line has no recipe_version_id.',
            'PLAN_LINE_PRODUCT_MISMATCH': 'plan_line product does not match product_id.',
            'RECIPE_MISSING': 'No active recipe version for product.',
            'FORCE_USE_BY_REQUIRED': 'use_by is required for this product.',
            'FORCE_PRODUCTION_DATE_REQUIRED': 'production_date is required for this product.',
        }
        if code in messages:
            return _err(messages[code], code)
        return _err(str(exc), 'VALIDATION')


@csrf_exempt
@require_GET
def preview_consume_api(request, run_id: int):
    run = _get_run(run_id)
    if run is None:
        return _err('Run not found.', 'VALIDATION', status_code=404)
    if not RecipeComponent.objects.filter(recipe_version_id=run.recipe_version_id).exists():
        return _err('Pinned recipe has no components.', 'RECIPE_MISSING', status_code=404)

    ingredients = _preview_ingredients(run)
    return api_success(
        'Consume preview fetched.',
        {
            'run_id': run.id,
            'recipe_version_id': run.recipe_version_id,
            'recipe_version_number': run.recipe_version.version_number,
            'process_loss': _dec(run.recipe_version.process_loss),
            'consume_location_id': run.station.default_consume_location_id,
            'ingredients': ingredients,
        },
    )


@csrf_exempt
@require_http_methods(['PUT'])
def consumptions_put_api(request, run_id: int):
    run = _get_run(run_id)
    if run is None:
        return _err('Run not found.', 'VALIDATION', status_code=404)
    if run.status != ProductionRunStatus.DRAFT:
        return _err('Only draft runs can set consumptions.', 'NOT_DRAFT', status_code=409)

    body = _parse_json_body(request)
    if body is None:
        return _err('Invalid JSON body.', 'VALIDATION')
    rows = body.get('consumptions')
    if not isinstance(rows, list):
        return _err('consumptions must be a list.', 'VALIDATION')

    cmap = _component_map(run.recipe_version_id)
    consume_location_id = run.station.default_consume_location_id

    try:
        to_create: list[ProductionRunConsumption] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                return _err(f'consumptions[{i}] must be an object.', 'VALIDATION')
            for key in ('component_product_id', 'lot_id', 'quantity', 'unit_id'):
                if row.get(key) in (None, ''):
                    return _err(f'consumptions[{i}].{key} is required.', 'VALIDATION')
            component_product_id = int(row['component_product_id'])
            lot_id = int(row['lot_id'])
            unit_id = int(row['unit_id'])
            qty = _parse_decimal(row['quantity'], f'consumptions[{i}].quantity')
            if qty <= 0:
                return _err('consumption quantity must be positive.', 'VALIDATION')
            component = cmap.get(component_product_id)
            if component is None:
                return _err(
                    f'component_product_id={component_product_id} not on pinned recipe.',
                    'VALIDATION',
                )
            try:
                lot = StockLot.objects.get(pk=lot_id)
            except StockLot.DoesNotExist:
                return _err(f'lot_id={lot_id} not found.', 'VALIDATION', status_code=404)
            if lot.product_id != component_product_id:
                return _err(
                    f'lot {lot_id} does not belong to component {component_product_id}.',
                    'VALIDATION',
                )
            bal = (
                StockBalance.objects
                .filter(lot_id=lot_id, location_id=consume_location_id)
                .first()
            )
            available = bal.quantity if bal else Decimal('0')
            if available < qty:
                return _err(
                    f'Insufficient stock for lot {lot_id}',
                    'INSUFFICIENT_STOCK',
                    lot_id=lot_id,
                    needed=_dec(qty),
                    available=_dec(available),
                )
            to_create.append(
                ProductionRunConsumption(
                    run=run,
                    component_product_id=component_product_id,
                    lot_id=lot_id,
                    quantity=qty,
                    unit_id=unit_id,
                    needed_qty=_needed_qty(component, run),
                )
            )

        with transaction.atomic():
            run.consumptions.all().delete()
            ProductionRunConsumption.objects.bulk_create(to_create)

        run = _get_run(run.id)
        return api_success('Consumptions saved.', _run_detail_payload(run))
    except ValueError as exc:
        return _err(str(exc), 'VALIDATION')


@csrf_exempt
@require_http_methods(['POST'])
def post_run_api(request, run_id: int):
    body = _parse_json_body(request) or {}
    idempotency_key = body.get('idempotency_key')
    if idempotency_key in (None, ''):
        return _err('idempotency_key is required.', 'VALIDATION')
    idempotency_key = str(idempotency_key)

    run = _get_run(run_id)
    if run is None:
        return _err('Run not found.', 'VALIDATION', status_code=404)

    if run.status == ProductionRunStatus.POSTED and run.idempotency_key == idempotency_key:
        return api_success('Run already posted.', _run_detail_payload(run))
    if run.status != ProductionRunStatus.DRAFT:
        return _err('Only draft runs can be posted.', 'NOT_DRAFT', status_code=409)

    cmap = _component_map(run.recipe_version_id)
    if not cmap:
        return _err('Pinned recipe has no components.', 'RECIPE_MISSING')

    consumptions = list(run.consumptions.select_related('lot').all())
    if not consumptions:
        return _err('No consumptions set.', 'INCOMPLETE_BOM')

    # Sum picked qty per component; block if under needed.
    picked: dict[int, Decimal] = {}
    for c in consumptions:
        picked[c.component_product_id] = picked.get(c.component_product_id, Decimal('0')) + c.quantity

    for product_id, component in cmap.items():
        needed = _needed_qty(component, run)
        got = picked.get(product_id, Decimal('0'))
        if got + Q6 < needed:
            return _err(
                f'Incomplete BOM for component {product_id}',
                'INCOMPLETE_BOM',
                component_product_id=product_id,
                needed=_dec(needed),
                picked=_dec(got),
            )

    consume_location_id = run.station.default_consume_location_id

    # Clamp use_by to earliest component lot if location extends_component_use_by.
    profile = LocationStockProfile.objects.filter(
        location_id=run.to_location_id,
    ).first()
    if profile and profile.extends_component_use_by:
        lot_use_bys = [c.lot.use_by for c in consumptions if c.lot.use_by]
        if lot_use_bys:
            earliest = min(lot_use_bys)
            if run.use_by is None or earliest < run.use_by:
                run.use_by = earliest

    try:
        with transaction.atomic():
            trace = run.trace_number or julian_trace_number(run.production_date)
            output_lot = StockLot.objects.create(
                product_id=run.product_id,
                recipe_version_id=run.recipe_version_id,
                trace_number=trace,
                origin=StockLotOrigin.PRODUCTION,
                production_date=run.production_date,
                use_by=run.use_by,
            )
            inputs = [
                {
                    'lot': c.lot,
                    'location_id': consume_location_id,
                    'quantity': c.quantity,
                    'unit_id': c.unit_id,
                }
                for c in consumptions
            ]
            output_entry, consumption_entries = stock_services.production(
                idempotency_key=idempotency_key,
                output_lot=output_lot,
                output_location_id=run.to_location_id,
                output_quantity=run.quantity_made,
                output_unit_id=run.unit_id,
                inputs=inputs,
                source_document_type='production_run',
                source_document_id=run.id,
                actor_user_id=body.get('actor_user_id') or run.actor_user_id,
            )
            for c, entry in zip(consumptions, consumption_entries):
                c.stock_entry_id = entry.id
                c.save(update_fields=['stock_entry_id'])

            run.status = ProductionRunStatus.POSTED
            run.idempotency_key = idempotency_key
            run.output_stock_entry_id = output_entry.id
            run.trace_number = trace
            run.posted_at = timezone.now()
            run.save(
                update_fields=[
                    'status',
                    'idempotency_key',
                    'output_stock_entry_id',
                    'trace_number',
                    'use_by',
                    'posted_at',
                ]
            )
    except StockValidationError as exc:
        msg = str(exc)
        code = 'INSUFFICIENT_STOCK' if 'insufficient' in msg.lower() or 'negative' in msg.lower() else 'VALIDATION'
        return _err(msg, code)
    except Exception as exc:
        return _err(str(exc), 'VALIDATION')

    run = _get_run(run.id)
    return api_success('Run posted.', _run_detail_payload(run), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def void_run_api(request, run_id: int):
    body = _parse_json_body(request) or {}
    run = _get_run(run_id)
    if run is None:
        return _err('Run not found.', 'VALIDATION', status_code=404)

    if run.status == ProductionRunStatus.VOID:
        return api_success('Run already void.', _run_detail_payload(run))

    if run.status == ProductionRunStatus.DRAFT:
        run.status = ProductionRunStatus.VOID
        run.voided_at = timezone.now()
        run.save(update_fields=['status', 'voided_at'])
        run = _get_run(run.id)
        return api_success('Run voided.', _run_detail_payload(run))

    # posted → reverse ledger
    void_key = body.get('idempotency_key') or f'void-run-{run.id}'
    try:
        with transaction.atomic():
            if run.output_stock_entry_id:
                stock_services.reversal(
                    idempotency_key=f'{void_key}:out',
                    entry=run.output_stock_entry,
                    actor_user_id=body.get('actor_user_id'),
                    remarks=body.get('reason'),
                )
            for i, c in enumerate(run.consumptions.all().order_by('id')):
                if c.stock_entry_id:
                    stock_services.reversal(
                        idempotency_key=f'{void_key}:in:{i}',
                        entry=c.stock_entry,
                        actor_user_id=body.get('actor_user_id'),
                        remarks=body.get('reason'),
                    )
            run.status = ProductionRunStatus.VOID
            run.voided_at = timezone.now()
            run.save(update_fields=['status', 'voided_at'])
    except StockValidationError as exc:
        return _err(str(exc), 'VALIDATION')

    run = _get_run(run.id)
    return api_success('Run voided.', _run_detail_payload(run))


# ---------------------------------------------------------------------------
# Downtime
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def station_downtime_api(request, code: str):
    station = _get_station(code)
    if station is None:
        return _err('Station not found.', 'VALIDATION', status_code=404)

    if request.method == 'GET':
        rows = ProductionDowntime.objects.filter(station=station).order_by('-start_at')[:200]
        return api_success('Downtime fetched.', [downtime_dict(r) for r in rows])

    body = _parse_json_body(request)
    if body is None:
        return _err('Invalid JSON body.', 'VALIDATION')
    try:
        start_at = _parse_dt(body.get('start_at'), 'start_at')
        if start_at is None:
            return _err('start_at is required.', 'VALIDATION')
        resource_id = body.get('resource_id')
        resource_id = int(resource_id) if resource_id not in (None, '') else None
        row = ProductionDowntime.objects.create(
            station=station,
            start_at=start_at,
            end_at=_parse_dt(body.get('end_at'), 'end_at'),
            resource_id=resource_id,
            shift=body.get('shift') or None,
            remarks=body.get('remarks') or None,
        )
        return api_success('Downtime created.', downtime_dict(row), status_code=201)
    except ValueError as exc:
        return _err(str(exc), 'VALIDATION')


@csrf_exempt
@require_http_methods(['DELETE'])
def downtime_delete_api(request, downtime_id: int):
    deleted, _ = ProductionDowntime.objects.filter(pk=downtime_id).delete()
    if not deleted:
        return _err('Downtime not found.', 'VALIDATION', status_code=404)
    return api_success('Downtime deleted.', {'id': downtime_id})
