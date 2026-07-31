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
from planning.models import (
    Plan,
    PlanAllocation,
    PlanEvent,
    PlanLine,
    PlanLineSource,
    PlanRequirement,
    PlanRun,
    PlanRunStatus,
    PlanStatus,
    PlanSupply,
    PlanSupplyKind,
)
from planning.services import allocate, explode, lifecycle


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


def _error_from_exc(exc):
    if isinstance(exc, PlanningStateError):
        return api_error(str(exc), status_code=409)
    if isinstance(exc, PlanningError):
        return api_error(str(exc), status_code=422)
    if isinstance(exc, ValueError):
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
        line = PlanLine.objects.create(
            plan=plan,
            product_id=int(body['product_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=int(body['unit_id']),
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
