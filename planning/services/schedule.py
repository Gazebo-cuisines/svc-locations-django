from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from product.models import ProductProduction
from locations.models import Location
from planning.models import (
    Plan,
    PlanRequirement,
    PlanResourceSlot,
    PlanRunStatus,
    PlanStatus,
    Resource,
    ResourceGroup,
)
from planning.services.exceptions import PlanningError, PlanningStateError

DEFAULT_JOB_MINUTES = Decimal('15')
DAY_START = time(8, 0, 0)


def _aware(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _day_start(slot_date: date) -> datetime:
    return _aware(datetime.combine(slot_date, DAY_START))


def _minutes_to_timedelta(minutes: Decimal) -> timedelta:
    # timedelta accepts float seconds
    secs = float(minutes) * 60.0
    if secs < 0:
        secs = 0
    return timedelta(seconds=secs)


def _job_duration_minutes(req: PlanRequirement, production: ProductProduction | None) -> Decimal:
    qty = req.gross_required or Decimal('0')
    if production is None:
        return DEFAULT_JOB_MINUTES
    if production.avg_minutes is not None and production.avg_minutes > 0:
        if production.avg_run_size and production.avg_run_size > 0 and qty > 0:
            return (qty / production.avg_run_size) * production.avg_minutes
        return production.avg_minutes
    if production.average_rate and production.average_rate > 0 and qty > 0:
        return qty / production.average_rate
    if production.avg_rate_product and production.avg_rate_product > 0 and qty > 0:
        return qty / production.avg_rate_product
    return DEFAULT_JOB_MINUTES


def _gap_minutes(production: ProductProduction | None, qty: Decimal) -> Decimal:
    if production is None or production.unitary_gap_time is None:
        return Decimal('0')
    # unitary gap: minutes per unit between jobs (legacy); apply once between jobs as qty * gap
    return max(Decimal('0'), production.unitary_gap_time * (qty or Decimal('1')))


def _dwell_minutes(production: ProductProduction | None, qty: Decimal) -> Decimal:
    if production is None or production.unitary_dwell_time is None:
        return Decimal('0')
    return max(Decimal('0'), production.unitary_dwell_time * (qty or Decimal('1')))


def _latest_complete_run(plan: Plan):
    return (
        plan.runs
        .filter(status=PlanRunStatus.COMPLETE)
        .order_by('-run_number')
        .first()
    )


def _production_map(product_ids: set[int]) -> dict[int, ProductProduction]:
    rows = ProductProduction.objects.filter(product_id__in=product_ids)
    return {row.product_id: row for row in rows}


def _retime_slots(slots: list[PlanResourceSlot], productions: dict[int, ProductProduction]) -> None:
    if not slots:
        return
    slot_date = slots[0].slot_date
    cursor = _day_start(slot_date)
    for index, slot in enumerate(slots):
        req = slot.requirement
        production = productions.get(req.product_id)
        qty = req.gross_required or Decimal('0')
        if index > 0:
            cursor = cursor + _minutes_to_timedelta(_gap_minutes(production, qty))
        duration = _job_duration_minutes(req, production)
        dwell = _dwell_minutes(production, qty)
        start = cursor
        finish = cursor + _minutes_to_timedelta(duration + dwell)
        slot.job_start = start
        slot.job_finish = finish
        slot.save(update_fields=['job_start', 'job_finish'])
        req.position = slot.position
        req.save(update_fields=['position'])
        cursor = finish


def create_resource(
    *,
    code: str,
    name: str,
    location_id: int,
    group_id: int | None = None,
    resource_id: int | None = None,
    is_active: bool = True,
) -> Resource:
    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        raise PlanningError('code and name are required')
    if location_id in (None, ''):
        raise PlanningError('location_id is required')
    if not Location.objects.filter(pk=location_id).exists():
        raise PlanningError(f'location_id={location_id} not found')
    if group_id is not None and not ResourceGroup.objects.filter(pk=group_id).exists():
        raise PlanningError(f'group_id={group_id} not found')
    if Resource.objects.filter(code=code).exists():
        raise PlanningError(f'resource code already exists: {code}')

    if resource_id is None:
        last = Resource.objects.order_by('-id').values_list('id', flat=True).first()
        resource_id = (last or 0) + 1
    elif Resource.objects.filter(pk=resource_id).exists():
        raise PlanningError(f'resource id={resource_id} already exists')

    return Resource.objects.create(
        id=resource_id,
        code=code,
        name=name,
        location_id=location_id,
        group_id=group_id,
        is_active=is_active,
    )


def update_resource(
    resource_id: int,
    *,
    code: str | None = None,
    name: str | None = None,
    location_id: int | None = None,
    group_id: int | None = None,
    clear_group: bool = False,
    is_active: bool | None = None,
) -> Resource:
    resource = Resource.objects.get(pk=resource_id)
    fields: list[str] = []
    if code is not None:
        code = code.strip()
        if not code:
            raise PlanningError('code cannot be empty')
        if Resource.objects.exclude(pk=resource_id).filter(code=code).exists():
            raise PlanningError(f'resource code already exists: {code}')
        resource.code = code
        fields.append('code')
    if name is not None:
        name = name.strip()
        if not name:
            raise PlanningError('name cannot be empty')
        resource.name = name
        fields.append('name')
    if location_id is not None:
        if not Location.objects.filter(pk=location_id).exists():
            raise PlanningError(f'location_id={location_id} not found')
        resource.location_id = location_id
        fields.append('location_id')
    if clear_group:
        resource.group_id = None
        fields.append('group_id')
    elif group_id is not None:
        if not ResourceGroup.objects.filter(pk=group_id).exists():
            raise PlanningError(f'group_id={group_id} not found')
        resource.group_id = group_id
        fields.append('group_id')
    if is_active is not None:
        resource.is_active = bool(is_active)
        fields.append('is_active')
    if fields:
        resource.save(update_fields=fields)
    return resource


@transaction.atomic
def sequence_plan(plan_id: int) -> list[PlanResourceSlot]:
    """Assign open requirements from the latest complete run onto default resources.

    Infinite capacity: order by product relative_plan_position, then level, id;
    forward-chain job_start/job_finish using ProductProduction rates/gap/dwell.
    """
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('cannot sequence a closed plan')
    run = _latest_complete_run(plan)
    if run is None:
        raise PlanningStateError('no complete run to sequence')

    requirements = list(
        PlanRequirement.objects
        .select_for_update(of=('self',))
        .filter(run_id=run.id, closed=False, default_resource_id__isnull=False)
        .select_related('default_resource')
    )
    if not requirements:
        raise PlanningError('no open requirements with a default resource')

    # Clear prior slots for these requirements
    PlanResourceSlot.objects.filter(requirement_id__in=[r.id for r in requirements]).delete()

    product_ids = {r.product_id for r in requirements}
    productions = _production_map(product_ids)

    def sort_key(req: PlanRequirement):
        prod = productions.get(req.product_id)
        rel = prod.relative_plan_position if prod and prod.relative_plan_position is not None else 10_000
        return (req.default_resource_id, rel, req.level, req.id)

    requirements.sort(key=sort_key)

    by_resource: dict[int, list[PlanRequirement]] = {}
    for req in requirements:
        by_resource.setdefault(req.default_resource_id, []).append(req)

    created: list[PlanResourceSlot] = []
    for resource_id, group in by_resource.items():
        resource = Resource.objects.get(pk=resource_id)
        if not resource.is_active:
            raise PlanningError(f'resource {resource.code} is inactive')
        slots: list[PlanResourceSlot] = []
        for index, req in enumerate(group, start=1):
            slot = PlanResourceSlot.objects.create(
                resource_id=resource_id,
                slot_date=plan.plan_date,
                requirement=req,
                position=index,
            )
            slots.append(slot)
            created.append(slot)
        _retime_slots(slots, productions)

    return list(
        PlanResourceSlot.objects
        .filter(pk__in=[s.id for s in created])
        .select_related('requirement', 'requirement__run', 'resource')
        .order_by('resource_id', 'position')
    )


@transaction.atomic
def reorder_resource_day(
    *,
    resource_id: int,
    slot_date: date,
    requirement_ids: list[int],
) -> list[PlanResourceSlot]:
    if not requirement_ids:
        raise PlanningError('requirement_ids is required')
    if len(set(requirement_ids)) != len(requirement_ids):
        raise PlanningError('requirement_ids must be unique')

    resource = Resource.objects.select_for_update().get(pk=resource_id)
    if not resource.is_active:
        raise PlanningError('resource is inactive')

    existing = list(
        PlanResourceSlot.objects
        .select_for_update(of=('self',))
        .filter(resource_id=resource_id, slot_date=slot_date)
        .select_related('requirement')
    )
    by_req = {s.requirement_id: s for s in existing}
    if set(requirement_ids) != set(by_req.keys()):
        raise PlanningError(
            'requirement_ids must match exactly the slots for this resource/day',
        )

    # Temporary positions to avoid unique constraint collisions
    for offset, slot in enumerate(existing, start=1):
        slot.position = -(offset)
        slot.save(update_fields=['position'])

    ordered: list[PlanResourceSlot] = []
    for index, req_id in enumerate(requirement_ids, start=1):
        slot = by_req[req_id]
        slot.position = index
        slot.save(update_fields=['position'])
        ordered.append(slot)

    productions = _production_map({s.requirement.product_id for s in ordered})
    _retime_slots(ordered, productions)
    return ordered


@transaction.atomic
def assign_requirement(
    *,
    requirement_id: int,
    resource_id: int,
    slot_date: date,
    position: int | None = None,
) -> PlanResourceSlot:
    req = PlanRequirement.objects.select_for_update().get(pk=requirement_id)
    if req.closed:
        raise PlanningStateError('cannot schedule a closed requirement')
    plan = req.run.plan
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('cannot schedule on a closed plan')

    resource = Resource.objects.select_for_update().get(pk=resource_id)
    if not resource.is_active:
        raise PlanningError('resource is inactive')

    PlanResourceSlot.objects.filter(requirement_id=requirement_id).delete()

    siblings = list(
        PlanResourceSlot.objects
        .select_for_update(of=('self',))
        .filter(resource_id=resource_id, slot_date=slot_date)
        .select_related('requirement')
        .order_by('position')
    )
    req_ids = [s.requirement_id for s in siblings]
    insert_at = len(req_ids) if position is None else max(0, min(position - 1, len(req_ids)))
    req_ids.insert(insert_at, requirement_id)

    # Wipe and recreate in order (avoids unique position collisions)
    PlanResourceSlot.objects.filter(resource_id=resource_id, slot_date=slot_date).delete()
    if req.default_resource_id != resource_id:
        req.default_resource_id = resource_id
        req.save(update_fields=['default_resource'])

    req_map = {r.id: r for r in PlanRequirement.objects.filter(pk__in=req_ids)}
    req_map[req.id] = req
    created: list[PlanResourceSlot] = []
    for index, rid in enumerate(req_ids, start=1):
        created.append(
            PlanResourceSlot.objects.create(
                resource=resource,
                slot_date=slot_date,
                requirement=req_map[rid],
                position=index,
            )
        )
    productions = _production_map({s.requirement.product_id for s in created})
    _retime_slots(created, productions)
    return created[insert_at]


@transaction.atomic
def unschedule_requirement(requirement_id: int) -> None:
    slot = (
        PlanResourceSlot.objects
        .select_for_update(of=('self',))
        .filter(requirement_id=requirement_id)
        .select_related('requirement')
        .first()
    )
    if slot is None:
        return
    resource_id = slot.resource_id
    slot_date = slot.slot_date
    slot.delete()

    remaining = list(
        PlanResourceSlot.objects
        .filter(resource_id=resource_id, slot_date=slot_date)
        .select_related('requirement')
        .order_by('position')
    )
    for index, row in enumerate(remaining, start=1):
        row.position = index
        row.save(update_fields=['position'])
    productions = _production_map({s.requirement.product_id for s in remaining})
    _retime_slots(remaining, productions)


def board_for_date(
    slot_date: date,
    *,
    resource_id: int | None = None,
) -> dict:
    resources_qs = Resource.objects.filter(is_active=True).order_by('code')
    if resource_id is not None:
        resources_qs = resources_qs.filter(pk=resource_id)
    resources = list(resources_qs)
    slots = list(
        PlanResourceSlot.objects
        .filter(slot_date=slot_date, resource_id__in=[r.id for r in resources])
        .select_related('requirement', 'resource')
        .order_by('resource_id', 'position')
    )
    by_res: dict[int, list[PlanResourceSlot]] = {r.id: [] for r in resources}
    for slot in slots:
        by_res.setdefault(slot.resource_id, []).append(slot)
    return {
        'slot_date': slot_date,
        'resources': resources,
        'slots_by_resource': by_res,
    }
