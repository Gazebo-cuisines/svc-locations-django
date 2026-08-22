from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.utils import DataError
from django.utils import timezone

from planning.adapters import product as product_adapter
from planning.adapters import recipe as recipe_adapter
from planning.models import (
    Plan,
    PlanEvent,
    PlanLine,
    PlanRequirement,
    PlanRun,
    PlanRunStatus,
    PlanStatus,
    Resource,
)
from planning.services import batching, netting
from planning.services.eligibility import required_min_shelf_life_days
from planning.services.exceptions import PlanningError, PlanningStateError
from recipe.utils import scaled_child_net

DRIVER_VERSION = 'explode-1.0'
MAX_BOM_DEPTH = 20


def _safe_resource_id(resource_id: int | None) -> int | None:
    if resource_id is None:
        return None
    if Resource.objects.filter(pk=resource_id).exists():
        return resource_id
    return None


def _consider_stock(line: PlanLine, product_consider: bool) -> bool:
    if line.override_consider_stock is not None:
        return bool(line.override_consider_stock)
    return product_consider


def _full_batches(line: PlanLine, product_flag: bool) -> bool:
    if line.override_full_batches is not None:
        return bool(line.override_full_batches)
    return product_flag


def _align_last(line: PlanLine, product_flag: bool) -> bool:
    if line.override_align_last_batch is not None:
        return bool(line.override_align_last_batch)
    return product_flag


@transaction.atomic
def run_explode(plan_id: int, *, actor_user_id: int | None = None) -> PlanRun:
    plan = Plan.objects.select_for_update().get(pk=plan_id)
    if plan.status == PlanStatus.CLOSED:
        raise PlanningStateError('cannot explode a closed plan')

    next_number = (
        plan.runs.order_by('-run_number').values_list('run_number', flat=True).first()
        or 0
    ) + 1

    now = timezone.now()
    run = PlanRun.objects.create(
        plan=plan,
        run_number=next_number,
        status=PlanRunStatus.RUNNING,
        driver_version=DRIVER_VERSION,
        started_at=now,
    )
    PlanEvent.objects.create(
        plan=plan,
        event_type='run_started',
        payload_json={'run_id': run.id, 'run_number': run.run_number},
        actor_user_id=actor_user_id,
    )

    try:
        with transaction.atomic():
            lines = list(plan.lines.order_by('sort_order', 'id'))
            if not lines:
                raise PlanningError('plan has no demand lines')

            for line in lines:
                _explode_line(plan, run, line)

        run.status = PlanRunStatus.COMPLETE
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'completed_at'])
        PlanEvent.objects.create(
            plan=plan,
            event_type='run_complete',
            payload_json={'run_id': run.id, 'run_number': run.run_number},
            actor_user_id=actor_user_id,
        )
        return run
    except Exception as exc:
        to_raise: Exception = exc
        if isinstance(exc, DataError):
            to_raise = PlanningError(
                'A recipe quantity overflowed during explode. '
                'Set batch_quantity on mix/spice/cook versions.'
            )
        run.status = PlanRunStatus.FAILED
        run.error_message = str(to_raise)
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'completed_at'])
        PlanEvent.objects.create(
            plan=plan,
            event_type='run_failed',
            payload_json={
                'run_id': run.id,
                'run_number': run.run_number,
                'error': str(to_raise),
            },
            actor_user_id=actor_user_id,
        )
        raise to_raise from exc


def _explode_line(plan: Plan, run: PlanRun, line: PlanLine) -> None:
    version_id = recipe_adapter.resolve_recipe_version_id(
        line.product_id,
        line.recipe_version_id,
    )
    process_loss = Decimal('1')
    recipe_spec = None
    if version_id is not None:
        recipe_spec = recipe_adapter.get_recipe_version(version_id)
        process_loss = recipe_spec.process_loss

    product = product_adapter.get_product_spec(
        line.product_id,
        process_loss=process_loss,
    )

    net = line.quantity
    if process_loss <= 0:
        process_loss = Decimal('1')
    gross = net / process_loss

    consider = _consider_stock(line, product.consider_stock_in_plan)
    net, gross, on_hand = netting.apply_stock_netting(
        net_required=net,
        gross_required=gross,
        process_loss=process_loss,
        product=product,
        plan_date=plan.plan_date,
        consider_stock=consider,
    )

    full = _full_batches(line, product.full_batches_only)
    align = _align_last(line, product.align_unitary_weight)
    batch_grosses = batching.split_batches(
        gross,
        full_batches_only=full,
        standard_batch_kg=product.standard_batch_kg,
        align_unitary_weight=align,
    )
    if not batch_grosses:
        batch_grosses = [Decimal('0')]

    for batch_number, batch_gross in enumerate(batch_grosses, start=1):
        batch_net = batch_gross * process_loss
        parent = PlanRequirement.objects.create(
            run=run,
            plan_line=line,
            parent_requirement=None,
            level=1,
            batch_number=batch_number,
            product_id=product.id,
            recipe_version_id=version_id,
            net_required=batch_net,
            gross_required=batch_gross,
            yield_factor=product.yield_factor,
            process_loss=process_loss,
            min_shelf_life_days=required_min_shelf_life_days(
                product,
                product.destination_location_id,
            ),
            source_location_id=product.source_location_id,
            destination_location_id=product.destination_location_id,
            default_resource_id=_safe_resource_id(product.default_resource_id),
            stock_on_hand=on_hand,
            balance=batch_gross,
            closed=False,
        )
        if recipe_spec is not None and batch_gross > 0:
            _explode_children(
                plan=plan,
                run=run,
                parent=parent,
                parent_gross=batch_gross,
                recipe_version_id=version_id,
                depth=1,
                ancestry={product.id},
                batch_number=batch_number,
                line=line,
            )


def _explode_children(
    *,
    plan: Plan,
    run: PlanRun,
    parent: PlanRequirement,
    parent_gross: Decimal,
    recipe_version_id: int,
    depth: int,
    ancestry: set[int],
    batch_number: int,
    line: PlanLine,
) -> None:
    if depth >= MAX_BOM_DEPTH:
        raise PlanningError(f'BOM depth exceeded MAX_BOM_DEPTH={MAX_BOM_DEPTH}')

    recipe_spec = recipe_adapter.get_recipe_version(recipe_version_id)
    bom_sum = sum((c.quantity for c in recipe_spec.components), Decimal('0'))
    for component in recipe_spec.components:
        if component.product_id in ancestry:
            raise PlanningError(
                f'BOM cycle detected at product {component.product_id}'
            )

        child_version_id = recipe_adapter.resolve_recipe_version_id(
            component.product_id,
            None,
        )
        child_loss = Decimal('1')
        child_recipe = None
        if child_version_id is not None:
            child_recipe = recipe_adapter.get_recipe_version(child_version_id)
            child_loss = child_recipe.process_loss

        child_product = product_adapter.get_product_spec(
            component.product_id,
            process_loss=child_loss,
        )
        if child_product.yield_factor <= 0:
            raise PlanningError(
                f'yield_factor must be > 0 for product {child_product.id}'
            )

        net_child = scaled_child_net(
            parent_gross,
            component.quantity,
            yield_factor=child_product.yield_factor,
            batch_quantity=recipe_spec.batch_quantity,
            bom_sum=bom_sum,
        )
        if child_loss <= 0:
            child_loss = Decimal('1')
        gross_child = net_child / child_loss

        consider = _consider_stock(line, child_product.consider_stock_in_plan)
        net_child, gross_child, on_hand = netting.apply_stock_netting(
            net_required=net_child,
            gross_required=gross_child,
            process_loss=child_loss,
            product=child_product,
            plan_date=plan.plan_date,
            consider_stock=consider,
        )

        full = _full_batches(line, child_product.full_batches_only)
        align = _align_last(line, child_product.align_unitary_weight)
        # Children inherit parent batch_number; do not re-split across new numbers
        # unless full_batches would change qty — apply split only as qty adjust on
        # same batch_number for MVP simplicity: one row per component per parent batch.
        if full and child_product.standard_batch_kg:
            parts = batching.split_batches(
                gross_child,
                full_batches_only=True,
                standard_batch_kg=child_product.standard_batch_kg,
                align_unitary_weight=align,
            )
            gross_child = sum(parts, Decimal('0'))
            net_child = gross_child * child_loss

        req = PlanRequirement.objects.create(
            run=run,
            plan_line=None,
            parent_requirement=parent,
            level=depth + 1,
            batch_number=batch_number,
            product_id=child_product.id,
            recipe_version_id=child_version_id,
            net_required=net_child,
            gross_required=gross_child,
            yield_factor=child_product.yield_factor,
            process_loss=child_loss,
            min_shelf_life_days=required_min_shelf_life_days(
                child_product,
                child_product.destination_location_id,
            ),
            source_location_id=child_product.source_location_id,
            destination_location_id=child_product.destination_location_id,
            default_resource_id=_safe_resource_id(child_product.default_resource_id),
            stock_on_hand=on_hand,
            balance=gross_child,
            closed=False,
        )

        if child_recipe is not None and gross_child > 0:
            _explode_children(
                plan=plan,
                run=run,
                parent=req,
                parent_gross=gross_child,
                recipe_version_id=child_version_id,
                depth=depth + 1,
                ancestry={*ancestry, child_product.id},
                batch_number=batch_number,
                line=line,
            )
