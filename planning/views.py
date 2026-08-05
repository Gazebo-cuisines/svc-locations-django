import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from planning.errors import PlanningError, PlanningStateError
from product.models import Product
from planning.models import (
    DemandProfile,
    Plan,
    PlanAllocation,
    PlanEvent,
    PlanLine,
    PlanLineSource,
    PlanRequirement,
    PlanResourceSlot,
    PlanRun,
    PlanRunStatus,
    PlanStatus,
    PlanSupply,
    PlanSupplyKind,
    Resource,
    ResourceGroup,
)
from planning.services import (
    allocate,
    explode,
    forecast,
    lifecycle,
    picking,
    portal,
    progress,
    schedule,
)


def _dec(value):
    return str(value) if value is not None else None


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


def _parse_date(value, field_name: str) -> date:
    if value in (None, ''):
        raise ValueError(f'{field_name} is required.')
    parsed = parse_date(str(value))
    if parsed is None:
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.') from exc
    return parsed


def _parse_datetime(value, field_name: str) -> datetime:
    if value in (None, ''):
        raise ValueError(f'{field_name} is required.')
    dt = parse_datetime(str(value))
    if dt is None:
        raise ValueError(f'Invalid datetime for {field_name}. Use ISO-8601.')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _optional_bool(value):
    if value is None:
        return None
    return bool(value)


def _parse_int(value, field_name: str) -> int:
    if value is None or value == '':
        raise ValueError(f'{field_name} is required')
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer') from exc


def _error_from_exc(exc):
    if isinstance(exc, PlanningStateError):
        return api_error(str(exc), status_code=409)
    if isinstance(exc, PlanningError):
        return api_error(str(exc), status_code=422)
    if isinstance(exc, (ValueError, TypeError)):
        return api_error(str(exc), status_code=400)
    if isinstance(exc, Plan.DoesNotExist):
        return api_error('Plan not found.', status_code=404)
    if isinstance(exc, PlanLine.DoesNotExist):
        return api_error('Plan line not found.', status_code=404)
    if isinstance(exc, PlanRun.DoesNotExist):
        return api_error('Plan run not found.', status_code=404)
    if isinstance(exc, PlanRequirement.DoesNotExist):
        return api_error('Requirement not found.', status_code=404)
    if isinstance(exc, PlanAllocation.DoesNotExist):
        return api_error('Allocation not found.', status_code=404)
    if isinstance(exc, PlanSupply.DoesNotExist):
        return api_error('Supply not found.', status_code=404)
    if isinstance(exc, Resource.DoesNotExist):
        return api_error('Resource not found.', status_code=404)
    if isinstance(exc, PlanResourceSlot.DoesNotExist):
        return api_error('Resource slot not found.', status_code=404)
    if isinstance(exc, DemandProfile.DoesNotExist):
        return api_error('Demand profile not found.', status_code=404)
    raise exc


def plan_dict(plan: Plan, *, include_lines: bool = False) -> dict:
    latest = (
        plan.runs.order_by('-run_number').first()
        if hasattr(plan, '_prefetched_objects_cache') and 'runs' in getattr(plan, '_prefetched_objects_cache', {})
        else plan.runs.order_by('-run_number').first()
    )
    data = {
        'id': plan.id,
        'plan_date': plan.plan_date.isoformat(),
        'location_id': plan.location_id,
        'status': plan.status,
        'remarks': plan.remarks,
        'published_at': (
            plan.published_at.isoformat() if plan.published_at else None
        ),
        'created_by_user_id': plan.created_by_user_id,
        'created_at': plan.created_at.isoformat() if plan.created_at else None,
        'updated_at': plan.updated_at.isoformat() if plan.updated_at else None,
        'line_count': getattr(plan, 'line_count', None),
        'latest_run_id': latest.id if latest else None,
        'latest_run_status': latest.status if latest else None,
    }
    if data['line_count'] is None:
        data['line_count'] = plan.lines.count()
    if include_lines:
        data['lines'] = [plan_line_dict(line) for line in plan.lines.order_by('sort_order', 'id')]
    return data


def plan_line_dict(line: PlanLine) -> dict:
    return {
        'id': line.id,
        'plan_id': line.plan_id,
        'product_id': line.product_id,
        'quantity': _dec(line.quantity),
        'unit_id': line.unit_id,
        'source': line.source,
        'override_consider_stock': line.override_consider_stock,
        'override_full_batches': line.override_full_batches,
        'override_align_last_batch': line.override_align_last_batch,
        'recipe_version_id': line.recipe_version_id,
        'sort_order': line.sort_order,
        'created_at': line.created_at.isoformat() if line.created_at else None,
        'updated_at': line.updated_at.isoformat() if line.updated_at else None,
    }


def plan_run_dict(run: PlanRun) -> dict:
    return {
        'id': run.id,
        'plan_id': run.plan_id,
        'run_number': run.run_number,
        'status': run.status,
        'driver_version': run.driver_version,
        'error_message': run.error_message,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'completed_at': run.completed_at.isoformat() if run.completed_at else None,
    }


def requirement_dict(req: PlanRequirement) -> dict:
    return {
        'id': req.id,
        'run_id': req.run_id,
        'plan_line_id': req.plan_line_id,
        'parent_requirement_id': req.parent_requirement_id,
        'level': req.level,
        'batch_number': req.batch_number,
        'position': req.position,
        'product_id': req.product_id,
        'recipe_version_id': req.recipe_version_id,
        'net_required': _dec(req.net_required),
        'gross_required': _dec(req.gross_required),
        'yield_factor': _dec(req.yield_factor),
        'process_loss': _dec(req.process_loss),
        'min_shelf_life_days': req.min_shelf_life_days,
        'source_location_id': req.source_location_id,
        'destination_location_id': req.destination_location_id,
        'default_resource_id': req.default_resource_id,
        'stock_on_hand': _dec(req.stock_on_hand),
        'balance': _dec(req.balance),
        'closed': req.closed,
        'created_at': req.created_at.isoformat() if req.created_at else None,
    }


def allocation_dict(row: PlanAllocation) -> dict:
    return {
        'id': row.id,
        'requirement_id': row.requirement_id,
        'lot_id': row.lot_id,
        'location_id': row.location_id,
        'quantity': _dec(row.quantity),
        'stock_reservation_id': row.stock_reservation_id,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def supply_dict(row: PlanSupply) -> dict:
    return {
        'id': row.id,
        'product_id': row.product_id,
        'location_id': row.location_id,
        'expected_at': row.expected_at.isoformat() if row.expected_at else None,
        'quantity': _dec(row.quantity),
        'unit_id': row.unit_id,
        'kind': row.kind,
        'source_document_type': row.source_document_type,
        'source_document_id': row.source_document_id,
        'remarks': row.remarks,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def event_dict(row: PlanEvent) -> dict:
    return {
        'id': row.id,
        'plan_id': row.plan_id,
        'event_type': row.event_type,
        'payload_json': row.payload_json,
        'actor_user_id': row.actor_user_id,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


# --- Plans ---


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def plans_collection_api(request):
    if request.method == 'GET':
        qs = Plan.objects.all().annotate(line_count=Count('lines')).order_by('-plan_date', '-id')
        status = request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        location_id = request.GET.get('location_id')
        if location_id:
            try:
                qs = qs.filter(location_id=int(location_id))
            except (TypeError, ValueError):
                return api_error('location_id must be an integer.')
        date_from = request.GET.get('plan_date_from')
        if date_from:
            try:
                qs = qs.filter(plan_date__gte=_parse_date(date_from, 'plan_date_from'))
            except ValueError as exc:
                return api_error(str(exc))
        date_to = request.GET.get('plan_date_to')
        if date_to:
            try:
                qs = qs.filter(plan_date__lte=_parse_date(date_to, 'plan_date_to'))
            except ValueError as exc:
                return api_error(str(exc))
        try:
            limit = min(int(request.GET.get('limit', 50)), 200)
            offset = max(int(request.GET.get('offset', 0)), 0)
        except (TypeError, ValueError):
            return api_error('limit and offset must be integers.')
        total = qs.count()
        items = [plan_dict(p) for p in qs[offset:offset + limit]]
        return api_success('Plans listed.', {'items': items, 'count': total})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    plan_date = None
    location_id = None
    try:
        plan_date = _parse_date(body.get('plan_date'), 'plan_date')
        location_id = int(body['location_id'])
        plan = lifecycle.create_plan(
            plan_date=plan_date,
            location_id=location_id,
            remarks=body.get('remarks'),
            actor_user_id=body.get('actor_user_id'),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except IntegrityError:
        existing = None
        if plan_date is not None and location_id is not None:
            existing = Plan.objects.filter(
                plan_date=plan_date,
                location_id=location_id,
            ).first()
        return api_error(
            'Plan already exists for this date and location',
            data={
                'plan_date': plan_date.isoformat() if plan_date else body.get('plan_date'),
                'location_id': location_id if location_id is not None else body.get('location_id'),
                'existing_plan_id': existing.id if existing else None,
            },
            status_code=409,
        )
    except (ValueError, TypeError, PlanningError, PlanningStateError) as exc:
        return _error_from_exc(exc)
    plan.line_count = 0
    return api_success('Plan created', plan_dict(plan), status_code=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH'])
def plan_detail_api(request, plan_id: int):
    plan = get_object_or_404(Plan, pk=plan_id)
    if request.method == 'GET':
        plan = (
            Plan.objects
            .annotate(line_count=Count('lines'))
            .prefetch_related('lines', 'runs')
            .get(pk=plan_id)
        )
        return api_success('Plan fetched.', plan_dict(plan, include_lines=True))

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    if plan.status != PlanStatus.DRAFT:
        return api_error('Plan remarks can only be updated while draft.', status_code=409)
    if 'remarks' in body:
        plan.remarks = body.get('remarks')
        plan.save(update_fields=['remarks', 'updated_at'])
    return api_success('Plan updated.', plan_dict(plan))


@csrf_exempt
@require_http_methods(['POST'])
def plan_lock_api(request, plan_id: int):
    body = _parse_json_body(request) or {}
    try:
        plan = lifecycle.lock_plan(plan_id, actor_user_id=body.get('actor_user_id'))
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success('Plan locked.', plan_dict(plan))


@csrf_exempt
@require_http_methods(['POST'])
def plan_publish_api(request, plan_id: int):
    body = _parse_json_body(request) or {}
    try:
        plan = lifecycle.publish_plan(
            plan_id,
            actor_user_id=body.get('actor_user_id'),
        )
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success('Plan published.', plan_dict(plan))


@csrf_exempt
@require_http_methods(['POST'])
def plan_close_api(request, plan_id: int):
    body = _parse_json_body(request) or {}
    try:
        plan = lifecycle.close_plan(plan_id, actor_user_id=body.get('actor_user_id'))
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success('Plan closed.', plan_dict(plan))


@csrf_exempt
@require_http_methods(['POST'])
def plan_reopen_api(request, plan_id: int):
    body = _parse_json_body(request) or {}
    try:
        plan = lifecycle.reopen_plan(plan_id, actor_user_id=body.get('actor_user_id'))
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success('Plan reopened.', plan_dict(plan))


@csrf_exempt
@require_http_methods(['POST'])
def plan_commit_api(request, plan_id: int):
    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        rows = allocate.commit_plan_allocations(
            plan_id,
            requirement_ids=body.get('requirement_ids'),
            actor_user_id=body.get('actor_user_id'),
        )
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Allocations committed',
        {
            'committed_count': len(rows),
            'allocations': [allocation_dict(r) for r in rows],
        },
    )


@csrf_exempt
@require_GET
def plan_events_api(request, plan_id: int):
    get_object_or_404(Plan, pk=plan_id)
    items = [
        event_dict(e)
        for e in PlanEvent.objects.filter(plan_id=plan_id).order_by('-created_at', '-id')
    ]
    return api_success('Events listed.', {'items': items})


# --- Lines ---


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def plan_lines_api(request, plan_id: int):
    plan = get_object_or_404(Plan, pk=plan_id)
    if request.method == 'GET':
        items = [plan_line_dict(line) for line in plan.lines.order_by('sort_order', 'id')]
        return api_success('Lines listed.', {'items': items})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        lifecycle.assert_lines_editable(plan)
        source = body.get('source', PlanLineSource.MANUAL)
        if source not in PlanLineSource.values:
            raise ValueError('source must be manual, order, or forecast')
        product_id = _parse_int(body.get('product_id'), 'product_id')
        raw_unit = body.get('unit_id')
        if raw_unit in (None, ''):
            product = Product.objects.filter(pk=product_id).first()
            if product is None or product.unit_id is None:
                raise ValueError('unit_id is required')
            unit_id = product.unit_id
        else:
            unit_id = _parse_int(raw_unit, 'unit_id')
        line = PlanLine.objects.create(
            plan=plan,
            product_id=product_id,
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=unit_id,
            source=source,
            override_consider_stock=_optional_bool(body.get('override_consider_stock')),
            override_full_batches=_optional_bool(body.get('override_full_batches')),
            override_align_last_batch=_optional_bool(body.get('override_align_last_batch')),
            recipe_version_id=(
                int(body['recipe_version_id'])
                if body.get('recipe_version_id') not in (None, '')
                else None
            ),
            sort_order=int(body.get('sort_order', 0)),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningStateError, IntegrityError) as exc:
        return _error_from_exc(exc) if not isinstance(exc, IntegrityError) else api_error(str(exc))
    return api_success('Line created.', plan_line_dict(line), status_code=201)


@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def plan_line_detail_api(request, plan_id: int, line_id: int):
    plan = get_object_or_404(Plan, pk=plan_id)
    line = get_object_or_404(PlanLine, pk=line_id, plan_id=plan_id)
    try:
        lifecycle.assert_lines_editable(plan)
    except PlanningStateError as exc:
        return _error_from_exc(exc)

    if request.method == 'DELETE':
        line.delete()
        return api_success('Line deleted.', {'id': line_id})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        if 'quantity' in body:
            line.quantity = _parse_decimal(body['quantity'], 'quantity')
        if 'unit_id' in body:
            line.unit_id = int(body['unit_id'])
        if 'product_id' in body:
            line.product_id = int(body['product_id'])
        if 'source' in body:
            if body['source'] not in PlanLineSource.values:
                raise ValueError('source must be manual, order, or forecast')
            line.source = body['source']
        if 'override_consider_stock' in body:
            line.override_consider_stock = _optional_bool(body.get('override_consider_stock'))
        if 'override_full_batches' in body:
            line.override_full_batches = _optional_bool(body.get('override_full_batches'))
        if 'override_align_last_batch' in body:
            line.override_align_last_batch = _optional_bool(body.get('override_align_last_batch'))
        if 'recipe_version_id' in body:
            line.recipe_version_id = (
                int(body['recipe_version_id'])
                if body.get('recipe_version_id') not in (None, '')
                else None
            )
        if 'sort_order' in body:
            line.sort_order = int(body['sort_order'])
        line.save()
    except (ValueError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Line updated.', plan_line_dict(line))


# --- Runs ---


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def plan_runs_api(request, plan_id: int):
    plan = get_object_or_404(Plan, pk=plan_id)
    if request.method == 'GET':
        items = [plan_run_dict(r) for r in plan.runs.order_by('-run_number')]
        return api_success('Runs listed.', {'items': items})

    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        run = explode.run_explode(plan_id, actor_user_id=body.get('actor_user_id'))
    except (PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        # Failed runs are persisted; surface failed run when available
        failed = (
            PlanRun.objects
            .filter(plan_id=plan_id, status=PlanRunStatus.FAILED)
            .order_by('-run_number')
            .first()
        )
        if failed and isinstance(exc, PlanningError) and not isinstance(exc, PlanningStateError):
            return api_error(
                str(exc),
                data={'run': plan_run_dict(failed)},
                status_code=422,
            )
        return _error_from_exc(exc)
    return api_success(
        'Plan run complete',
        {
            'run': plan_run_dict(run),
            'requirement_count': run.requirements.count(),
        },
        status_code=201,
    )


@csrf_exempt
@require_GET
def plan_run_detail_api(request, plan_id: int, run_id: int):
    run = get_object_or_404(PlanRun, pk=run_id, plan_id=plan_id)
    return api_success('Run fetched.', plan_run_dict(run))


@csrf_exempt
@require_GET
def plan_run_requirements_api(request, plan_id: int, run_id: int):
    run = get_object_or_404(PlanRun, pk=run_id, plan_id=plan_id)
    items = [
        requirement_dict(r)
        for r in run.requirements.order_by('level', 'batch_number', 'id')
    ]
    return api_success('Requirements listed.', {'items': items})


@csrf_exempt
@require_GET
def plan_run_picking_list_api(request, plan_id: int, run_id: int):
    run = get_object_or_404(PlanRun, pk=run_id, plan_id=plan_id)
    from_location = request.GET.get('from_location')
    if from_location is not None:
        from_location = from_location.strip() or None
    to_location = request.GET.get('to_location')
    if to_location is not None:
        to_location = to_location.strip() or None
    data = picking.build_picking_list(
        run,
        from_location=from_location,
        to_location=to_location,
    )
    return api_success('Picking list fetched successfully.', data)


@csrf_exempt
@require_GET
def portal_today_api(request):
    location = request.GET.get('location')
    mode = request.GET.get('mode') or 'outbound'
    plan_date = None
    raw_date = request.GET.get('plan_date')
    if raw_date not in (None, ''):
        try:
            plan_date = _parse_date(raw_date, 'plan_date')
        except ValueError as exc:
            return api_error(str(exc), status_code=400)
    try:
        data = portal.portal_today(
            location=location or '',
            plan_date=plan_date,
            mode=mode,
        )
    except PlanningError as exc:
        return _error_from_exc(exc)
    return api_success('Portal today fetched successfully.', data)


@csrf_exempt
@require_GET
def plan_progress_api(request, plan_id: int):
    location = request.GET.get('location')
    try:
        data = progress.build_plan_progress(
            plan_id,
            location=location,
        )
    except PlanningError as exc:
        return _error_from_exc(exc)
    return api_success('Plan progress fetched successfully.', data)


# --- Allocations ---


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def requirement_allocations_api(request, requirement_id: int):
    req = get_object_or_404(PlanRequirement, pk=requirement_id)
    if request.method == 'GET':
        items = [
            allocation_dict(a)
            for a in req.allocations.order_by('id')
        ]
        return api_success('Allocations listed.', {'items': items})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        row = allocate.soft_allocate(
            requirement_id,
            lot_id=int(body['lot_id']),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningError, PlanningStateError) as exc:
        return _error_from_exc(exc)
    return api_success('Allocation created.', allocation_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['DELETE'])
def allocation_detail_api(request, allocation_id: int):
    try:
        allocate.delete_soft_allocation(allocation_id)
    except (PlanningError, PlanAllocation.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success('Allocation deleted.', {'id': allocation_id})


# --- Supply ---


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def supply_collection_api(request):
    if request.method == 'GET':
        qs = PlanSupply.objects.all().order_by('expected_at', 'id')
        if request.GET.get('product_id'):
            try:
                qs = qs.filter(product_id=int(request.GET['product_id']))
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        if request.GET.get('location_id'):
            try:
                qs = qs.filter(location_id=int(request.GET['location_id']))
            except (TypeError, ValueError):
                return api_error('location_id must be an integer.')
        if request.GET.get('expected_at_from'):
            try:
                qs = qs.filter(
                    expected_at__gte=_parse_datetime(
                        request.GET['expected_at_from'], 'expected_at_from',
                    )
                )
            except ValueError as exc:
                return api_error(str(exc))
        if request.GET.get('expected_at_to'):
            try:
                qs = qs.filter(
                    expected_at__lte=_parse_datetime(
                        request.GET['expected_at_to'], 'expected_at_to',
                    )
                )
            except ValueError as exc:
                return api_error(str(exc))
        items = [supply_dict(r) for r in qs]
        return api_success('Supply listed.', {'items': items})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        kind = body['kind']
        if kind not in PlanSupplyKind.values:
            raise ValueError('invalid supply kind')
        row = PlanSupply.objects.create(
            product_id=int(body['product_id']),
            location_id=int(body['location_id']),
            expected_at=_parse_datetime(body.get('expected_at'), 'expected_at'),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
            kind=kind,
            source_document_type=body.get('source_document_type'),
            source_document_id=body.get('source_document_id'),
            remarks=body.get('remarks'),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, IntegrityError) as exc:
        return api_error(str(exc))
    return api_success('Supply created.', supply_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['PATCH', 'DELETE'])
def supply_detail_api(request, supply_id: int):
    row = get_object_or_404(PlanSupply, pk=supply_id)
    if request.method == 'DELETE':
        row.delete()
        return api_success('Supply deleted.', {'id': supply_id})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        if 'product_id' in body:
            row.product_id = int(body['product_id'])
        if 'location_id' in body:
            row.location_id = int(body['location_id'])
        if 'expected_at' in body:
            row.expected_at = _parse_datetime(body.get('expected_at'), 'expected_at')
        if 'quantity' in body:
            row.quantity = _parse_decimal(body['quantity'], 'quantity')
        if 'unit_id' in body:
            row.unit_id = int(body['unit_id'])
        if 'kind' in body:
            if body['kind'] not in PlanSupplyKind.values:
                raise ValueError('invalid supply kind')
            row.kind = body['kind']
        if 'source_document_type' in body:
            row.source_document_type = body.get('source_document_type')
        if 'source_document_id' in body:
            row.source_document_id = body.get('source_document_id')
        if 'remarks' in body:
            row.remarks = body.get('remarks')
        row.save()
    except (ValueError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Supply updated.', supply_dict(row))


# --- Resources / board (Chunk 9) ---


def resource_dict(row: Resource) -> dict:
    location = getattr(row, 'location', None)
    group = getattr(row, 'group', None)
    return {
        'id': row.id,
        'code': row.code,
        'name': row.name,
        'location_id': row.location_id,
        'location_name': location.name if location is not None else None,
        'group_id': row.group_id,
        'group_name': group.name if group is not None else None,
        'is_active': row.is_active,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def resource_group_dict(row: ResourceGroup) -> dict:
    return {
        'id': row.id,
        'name': row.name,
    }


def _resources_grouped_by_location(rows: list[Resource]) -> list[dict]:
    """Match Access Resources form: sections by container (location)."""
    sections: dict[int, dict] = {}
    order: list[int] = []
    for row in rows:
        loc_id = row.location_id
        if loc_id not in sections:
            location = getattr(row, 'location', None)
            sections[loc_id] = {
                'location_id': loc_id,
                'location_name': location.name if location is not None else None,
                'items': [],
            }
            order.append(loc_id)
        sections[loc_id]['items'].append(resource_dict(row))
    order.sort(
        key=lambda lid: (sections[lid]['location_name'] or '').lower(),
    )
    return [sections[lid] for lid in order]


def slot_dict(row: PlanResourceSlot) -> dict:
    req = row.requirement
    return {
        'id': row.id,
        'resource_id': row.resource_id,
        'slot_date': row.slot_date.isoformat(),
        'requirement_id': row.requirement_id,
        'position': row.position,
        'job_start': row.job_start.isoformat() if row.job_start else None,
        'job_finish': row.job_finish.isoformat() if row.job_finish else None,
        'product_id': req.product_id if req else None,
        'gross_required': _dec(req.gross_required) if req else None,
        'level': req.level if req else None,
        'plan_id': req.run.plan_id if req and req.run_id else None,
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def resources_collection_api(request):
    if request.method == 'GET':
        qs = (
            Resource.objects.select_related('location', 'group')
            .all()
            .order_by('location__name', 'name', 'id')
        )
        active = request.GET.get('is_active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() in ('1', 'true', 'yes'))
        location_id = request.GET.get('location_id')
        if location_id not in (None, ''):
            try:
                qs = qs.filter(location_id=int(location_id))
            except (TypeError, ValueError):
                return api_error('location_id must be an integer.')
        group_id = request.GET.get('group_id')
        if group_id not in (None, ''):
            try:
                qs = qs.filter(group_id=int(group_id))
            except (TypeError, ValueError):
                return api_error('group_id must be an integer.')

        rows = list(qs)
        group_by = (request.GET.get('group_by') or '').strip().lower()
        if group_by in ('location', 'container'):
            return api_success(
                'Resources listed by location.',
                {'sections': _resources_grouped_by_location(rows)},
            )
        if group_by and group_by not in ('', 'none', 'flat'):
            return api_error(
                'group_by must be location, container, or omitted.',
                status_code=400,
            )
        return api_success(
            'Resources listed.',
            {'items': [resource_dict(r) for r in rows]},
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        resource_id = body.get('id')
        if resource_id is not None:
            resource_id = int(resource_id)
        group_id = body.get('group_id')
        if group_id is not None:
            group_id = int(group_id)
        row = schedule.create_resource(
            code=str(body.get('code') or ''),
            name=str(body.get('name') or ''),
            location_id=int(body['location_id']) if body.get('location_id') is not None else None,
            group_id=group_id,
            resource_id=resource_id,
            is_active=bool(body['is_active']) if 'is_active' in body else True,
        )
    except (PlanningError, PlanningStateError, TypeError, ValueError) as exc:
        return _error_from_exc(exc)
    return api_success('Resource created.', resource_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def resource_detail_api(request, resource_id: int):
    row = get_object_or_404(
        Resource.objects.select_related('location', 'group'),
        pk=resource_id,
    )
    if request.method == 'GET':
        return api_success('Resource fetched.', resource_dict(row))
    if request.method == 'DELETE':
        if row.slots.exists():
            return api_error('Resource has slots; unschedule first.', status_code=409)
        row.delete()
        return api_success('Resource deleted.', {'id': resource_id})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        clear_group = 'group_id' in body and body.get('group_id') is None
        group_id = None
        if 'group_id' in body and body.get('group_id') is not None:
            group_id = int(body['group_id'])
        location_id = None
        if 'location_id' in body:
            location_id = int(body['location_id'])
        row = schedule.update_resource(
            resource_id,
            code=body.get('code') if 'code' in body else None,
            name=body.get('name') if 'name' in body else None,
            location_id=location_id,
            group_id=group_id,
            clear_group=clear_group,
            is_active=body.get('is_active') if 'is_active' in body else None,
        )
    except (PlanningError, PlanningStateError, Resource.DoesNotExist, TypeError, ValueError) as exc:
        return _error_from_exc(exc)
    return api_success('Resource updated.', resource_dict(row))


@require_GET
def resource_groups_list_api(request):
    items = [resource_group_dict(g) for g in ResourceGroup.objects.all().order_by('name')]
    return api_success('Resource groups listed.', {'items': items})


@csrf_exempt
@require_GET
def board_api(request):
    try:
        slot_date = _parse_date(request.GET.get('slot_date'), 'slot_date')
        resource_id = None
        if request.GET.get('resource_id'):
            resource_id = int(request.GET['resource_id'])
    except (ValueError, TypeError) as exc:
        return api_error(str(exc))

    board = schedule.board_for_date(slot_date, resource_id=resource_id)
    columns = []
    for resource in board['resources']:
        slots = board['slots_by_resource'].get(resource.id, [])
        columns.append({
            'resource': resource_dict(resource),
            'slots': [slot_dict(s) for s in slots],
        })
    return api_success(
        'Board loaded.',
        {
            'slot_date': slot_date.isoformat(),
            'columns': columns,
        },
    )


@csrf_exempt
@require_http_methods(['POST'])
def board_sequence_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        plan_id = int(body['plan_id'])
        slots = schedule.sequence_plan(plan_id)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningError, PlanningStateError, Plan.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Plan sequenced onto resources.',
        {'items': [slot_dict(s) for s in slots], 'count': len(slots)},
    )


@csrf_exempt
@require_http_methods(['POST'])
def board_reorder_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        slots = schedule.reorder_resource_day(
            resource_id=int(body['resource_id']),
            slot_date=_parse_date(body.get('slot_date'), 'slot_date'),
            requirement_ids=[int(x) for x in body['requirement_ids']],
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningError, PlanningStateError, Resource.DoesNotExist) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Board reordered.',
        {'items': [slot_dict(s) for s in slots]},
    )


@csrf_exempt
@require_http_methods(['POST'])
def board_assign_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        position = body.get('position')
        slot = schedule.assign_requirement(
            requirement_id=int(body['requirement_id']),
            resource_id=int(body['resource_id']),
            slot_date=_parse_date(body.get('slot_date'), 'slot_date'),
            position=int(position) if position is not None else None,
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (
        ValueError,
        TypeError,
        PlanningError,
        PlanningStateError,
        PlanRequirement.DoesNotExist,
        Resource.DoesNotExist,
    ) as exc:
        return _error_from_exc(exc)
    return api_success('Requirement assigned.', slot_dict(slot), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def board_unschedule_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        requirement_id = int(body['requirement_id'])
        schedule.unschedule_requirement(requirement_id)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningError, PlanningStateError) as exc:
        return _error_from_exc(exc)
    return api_success('Requirement unscheduled.', {'requirement_id': requirement_id})


# --- Forecast / shortage (Chunk 10) ---


def demand_profile_dict(row: DemandProfile) -> dict:
    return {
        'id': row.id,
        'product_id': row.product_id,
        'weekday': row.weekday,
        'mean_quantity': _dec(row.mean_quantity),
        'sample_count': row.sample_count,
        'computed_at': row.computed_at.isoformat() if row.computed_at else None,
    }


def _dec_row_dates(row: dict) -> dict:
    out = dict(row)
    if isinstance(out.get('date'), date):
        out['date'] = out['date'].isoformat()
    for key in ('demand', 'available', 'on_hand', 'shortage', 'required'):
        if key in out and out[key] is not None and not isinstance(out[key], str):
            out[key] = _dec(out[key])
    return out


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def demand_profiles_collection_api(request):
    if request.method == 'GET':
        product_id = None
        if request.GET.get('product_id'):
            try:
                product_id = int(request.GET['product_id'])
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        items = [
            demand_profile_dict(r)
            for r in forecast.list_demand_profiles(product_id=product_id)
        ]
        return api_success('Demand profiles listed.', {'items': items})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        row = forecast.upsert_demand_profile(
            product_id=int(body['product_id']),
            weekday=int(body['weekday']),
            mean_quantity=_parse_decimal(body['mean_quantity'], 'mean_quantity'),
            sample_count=int(body.get('sample_count', 1)),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, TypeError, PlanningError) as exc:
        return _error_from_exc(exc)
    return api_success('Demand profile saved.', demand_profile_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def demand_profile_detail_api(request, profile_id: int):
    row = get_object_or_404(DemandProfile, pk=profile_id)
    if request.method == 'GET':
        return api_success('Demand profile fetched.', demand_profile_dict(row))
    if request.method == 'DELETE':
        forecast.delete_demand_profile(profile_id)
        return api_success('Demand profile deleted.', {'id': profile_id})

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        row = forecast.upsert_demand_profile(
            product_id=int(body.get('product_id', row.product_id)),
            weekday=int(body.get('weekday', row.weekday)),
            mean_quantity=_parse_decimal(
                body['mean_quantity'] if 'mean_quantity' in body else row.mean_quantity,
                'mean_quantity',
            ),
            sample_count=int(body.get('sample_count', row.sample_count)),
        )
    except (ValueError, TypeError, PlanningError) as exc:
        return _error_from_exc(exc)
    return api_success('Demand profile updated.', demand_profile_dict(row))


@csrf_exempt
@require_GET
def forecast_horizon_api(request):
    try:
        start = _parse_date(
            request.GET.get('start_date') or timezone.localdate().isoformat(),
            'start_date',
        )
        days = int(request.GET.get('days', 5))
        product_id = None
        if request.GET.get('product_id'):
            product_id = int(request.GET['product_id'])
        rows = forecast.project_horizon(
            start_date=start,
            days=days,
            product_id=product_id,
        )
    except (ValueError, TypeError, PlanningError) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Forecast horizon.',
        {
            'start_date': start.isoformat(),
            'days': days,
            'items': [_dec_row_dates(r) for r in rows],
        },
    )


@csrf_exempt
@require_GET
def forecast_shortage_api(request):
    try:
        start = _parse_date(
            request.GET.get('start_date') or timezone.localdate().isoformat(),
            'start_date',
        )
        days = int(request.GET.get('days', 5))
        product_id = None
        if request.GET.get('product_id'):
            product_id = int(request.GET['product_id'])
        rows = forecast.shortage_report(
            start_date=start,
            days=days,
            product_id=product_id,
        )
    except (ValueError, TypeError, PlanningError) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Shortage report.',
        {
            'start_date': start.isoformat(),
            'days': days,
            'items': [_dec_row_dates(r) for r in rows],
        },
    )


@csrf_exempt
@require_http_methods(['POST'])
def forecast_age_api(request):
    body = _parse_json_body(request) or {}
    try:
        as_of = None
        if body.get('as_of'):
            as_of = _parse_date(body.get('as_of'), 'as_of')
        results = forecast.age_open_plans(
            as_of=as_of,
            actor_user_id=body.get('actor_user_id'),
        )
    except (ValueError, TypeError, PlanningError, PlanningStateError) as exc:
        return _error_from_exc(exc)
    return api_success(
        'Aged plans rolled over.',
        {
            'items': [
                {
                    'source_plan_id': r['source_plan_id'],
                    'target_plan_id': r['target_plan_id'],
                    'target_plan_date': r['target_plan_date'].isoformat(),
                }
                for r in results
            ],
            'count': len(results),
        },
    )
