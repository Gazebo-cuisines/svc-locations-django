from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from planning.adapters import product as product_adapter
from planning.adapters import stock as stock_adapter
from planning.adapters.locations import plannable_locations
from planning.models import (
    Plan,
    PlanAllocation,
    PlanEvent,
    PlanRequirement,
    PlanRunStatus,
    PlanStatus,
)
from planning.services import eligibility
from planning.services.exceptions import PlanningError, PlanningStateError


def _latest_complete_run(plan: Plan):
    return (
        plan.runs
        .filter(status=PlanRunStatus.COMPLETE)
        .order_by('-run_number')
        .first()
    )


@transaction.atomic
def soft_allocate(
    requirement_id: int,
    *,
    lot_id: int,
    location_id: int,
    quantity: Decimal,
) -> PlanAllocation:
    if quantity <= 0:
        raise PlanningError('allocation quantity must be positive')

    req = (
        PlanRequirement.objects
        .select_related('run__plan', 'product')
        .select_for_update()
        .get(pk=requirement_id)
    )
    plan = req.run.plan
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('cannot allocate on a closed plan')

    latest = _latest_complete_run(plan)
    if latest is None or req.run_id != latest.id:
        raise PlanningStateError(
            'allocations only allowed on the latest complete run'
        )

    product = product_adapter.get_product_spec(req.product_id)
    location_ids = plannable_locations(product)
    if location_id not in location_ids:
        raise PlanningError('location is not plannable for this product')

    lots = stock_adapter.lots_for_product(req.product_id, [location_id])
    eligible = eligibility.eligible_lots(
        lots,
        plan_date=plan.plan_date,
        product=product,
        destination_location_id=req.destination_location_id,
    )
    match = next((lot for lot in eligible if lot.lot_id == lot_id), None)
    if match is None:
        raise PlanningError(
            'lot fails FEFO/shelf-life eligibility or has no ATP'
        )
    if quantity > match.atp:
        raise PlanningError(
            f'quantity {quantity} exceeds lot ATP {match.atp}'
        )

    alloc, _created = PlanAllocation.objects.update_or_create(
        requirement=req,
        lot_id=lot_id,
        location_id=location_id,
        defaults={
            'quantity': quantity,
            'stock_reservation_id': None,
        },
    )
    return alloc


@transaction.atomic
def delete_soft_allocation(allocation_id: int) -> None:
    alloc = PlanAllocation.objects.select_for_update().get(pk=allocation_id)
    if alloc.stock_reservation_id is not None:
        raise PlanningError('committed allocations cannot be deleted; release via close')
    alloc.delete()


@transaction.atomic
def commit_plan_allocations(
    plan_id: int,
    *,
    requirement_ids: list[int] | None = None,
    actor_user_id: int | None = None,
) -> list[PlanAllocation]:
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('cannot commit a closed plan')

    latest = _latest_complete_run(plan)
    if latest is None:
        raise PlanningStateError('no complete run to commit')

    qs = (
        PlanAllocation.objects
        .select_for_update()
        .filter(
            requirement__run_id=latest.id,
            stock_reservation__isnull=True,
        )
        .select_related('requirement')
    )
    if requirement_ids is not None:
        qs = qs.filter(requirement_id__in=requirement_ids)

    committed: list[PlanAllocation] = []
    for alloc in qs:
        product = product_adapter.get_product_spec(alloc.requirement.product_id)
        reservation = stock_adapter.create_stock_reservation(
            lot_id=alloc.lot_id,
            location_id=alloc.location_id,
            quantity=alloc.quantity,
            unit_id=product.unit_id,
            plan_id=plan.id,
            requirement_id=alloc.requirement_id,
        )
        alloc.stock_reservation_id = reservation.id
        alloc.save(update_fields=['stock_reservation', 'updated_at'])
        committed.append(alloc)

    PlanEvent.objects.create(
        plan=plan,
        event_type='committed',
        payload_json={'count': len(committed), 'run_id': latest.id},
        actor_user_id=actor_user_id,
    )
    return committed
