from __future__ import annotations

from django.db import transaction

from planning.adapters import stock as stock_adapter
from planning.models import (
    Plan,
    PlanAllocation,
    PlanEvent,
    PlanLine,
    PlanRequirement,
    PlanStatus,
)
from planning.services.exceptions import PlanningStateError


@transaction.atomic
def lock_plan(plan_id: int, *, actor_user_id: int | None = None) -> Plan:
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status != PlanStatus.DRAFT:
        raise PlanningStateError('only draft plans can be locked')
    plan.status = PlanStatus.LOCKED
    plan.save(update_fields=['status', 'updated_at'])
    PlanEvent.objects.create(
        plan=plan,
        event_type='locked',
        payload_json={},
        actor_user_id=actor_user_id,
    )
    return plan


@transaction.atomic
def close_plan(plan_id: int, *, actor_user_id: int | None = None) -> Plan:
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('plan is already closed')

    allocs = (
        PlanAllocation.objects
        .select_for_update()
        .filter(requirement__run__plan_id=plan.id)
    )
    for alloc in allocs:
        if alloc.stock_reservation_id:
            stock_adapter.release_stock_reservation(alloc.stock_reservation_id)
            alloc.stock_reservation_id = None
            alloc.save(update_fields=['stock_reservation', 'updated_at'])

    PlanRequirement.objects.filter(run__plan_id=plan.id, closed=False).update(
        closed=True,
    )

    plan.status = PlanStatus.CLOSED
    plan.save(update_fields=['status', 'updated_at'])
    PlanEvent.objects.create(
        plan=plan,
        event_type='closed',
        payload_json={},
        actor_user_id=actor_user_id,
    )
    return plan


@transaction.atomic
def reopen_plan(plan_id: int, *, actor_user_id: int | None = None) -> Plan:
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status != PlanStatus.CLOSED:
        raise PlanningStateError('only closed plans can be reopened')
    plan.status = PlanStatus.DRAFT
    plan.save(update_fields=['status', 'updated_at'])
    PlanEvent.objects.create(
        plan=plan,
        event_type='reopened',
        payload_json={},
        actor_user_id=actor_user_id,
    )
    return plan


@transaction.atomic
def create_plan(
    *,
    plan_date,
    location_id: int,
    remarks: str | None = None,
    actor_user_id: int | None = None,
) -> Plan:
    plan = Plan.objects.create(
        plan_date=plan_date,
        location_id=location_id,
        status=PlanStatus.DRAFT,
        remarks=remarks,
        created_by_user_id=actor_user_id,
    )
    PlanEvent.objects.create(
        plan=plan,
        event_type='created',
        payload_json={'location_id': location_id},
        actor_user_id=actor_user_id,
    )
    return plan


def assert_lines_editable(plan: Plan) -> None:
    if plan.status != PlanStatus.DRAFT:
        raise PlanningStateError('plan lines can only be edited while draft')


@transaction.atomic
def rollover_open_lines(
    source_plan_id: int,
    *,
    target_plan_date,
    actor_user_id: int | None = None,
) -> Plan:
    """Close source plan and copy unfinished open lines to a new/next plan (D2)."""
    source = Plan.objects.select_for_update().get(pk=source_plan_id)
    open_lines = list(source.lines.all())
    if source.status != PlanStatus.CLOSED:
        close_plan(source.id, actor_user_id=actor_user_id)
        source.refresh_from_db()

    target, _created = Plan.objects.get_or_create(
        plan_date=target_plan_date,
        location_id=source.location_id,
        defaults={
            'status': PlanStatus.DRAFT,
            'remarks': f'Rollover from plan {source.id}',
            'created_by_user_id': actor_user_id,
        },
    )
    if target.status == PlanStatus.CLOSED:
        reopen_plan(target.id, actor_user_id=actor_user_id)
        target.refresh_from_db()

    for line in open_lines:
        PlanLine.objects.create(
            plan=target,
            product_id=line.product_id,
            quantity=line.quantity,
            unit_id=line.unit_id,
            source=line.source,
            override_consider_stock=line.override_consider_stock,
            override_full_batches=line.override_full_batches,
            override_align_last_batch=line.override_align_last_batch,
            recipe_version_id=line.recipe_version_id,
            sort_order=line.sort_order,
        )

    PlanEvent.objects.create(
        plan=source,
        event_type='rolled_over',
        payload_json={'target_plan_id': target.id},
        actor_user_id=actor_user_id,
    )
    PlanEvent.objects.create(
        plan=target,
        event_type='created',
        payload_json={'rollover_from': source.id},
        actor_user_id=actor_user_id,
    )
    return target
