from __future__ import annotations

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
from product.models import Product, Unit
from recipe.utils import batch_scale_denom, scaled_child_net
from stock_ledger.util.conversions import StockValidationError, to_product_unit

DRIVER_VERSION = 'explode-1.4'
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


def _qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def _is_one(value: Decimal) -> bool:
    return value == 1


def _recipe_yield_pct(keep: Decimal) -> str:
    pct = (keep * Decimal('100')).quantize(Decimal('0.01'))
    return _qty(pct)


def _gross_step(net: Decimal, keep: Decimal, gross: Decimal) -> dict:
    pct = _recipe_yield_pct(keep)
    return {
        'op': 'gross',
        'formula': 'net / recipe_yield',
        'from': f'{_qty(net)} / {_qty(keep)} (recipe yield {pct}%)',
        'to': _qty(gross),
    }


def _scale_bom_labels(
    parent_gross: Decimal,
    bom_qty: Decimal,
    denom: Decimal | None,
    yf: Decimal,
) -> tuple[str, str]:
    p, b = _qty(parent_gross), _qty(bom_qty)
    skip_y = _is_one(yf)
    if denom:
        d = _qty(denom)
        if skip_y:
            return 'parent_gross × bom_qty / batch_quantity', f'{p} × {b} / {d}'
        return (
            'parent_gross × bom_qty / batch_quantity / product_yield',
            f'{p} × {b} / {d} / {_qty(yf)}',
        )
    if skip_y:
        return 'parent_gross × bom_qty', f'{p} × {b}'
    return (
        'parent_gross × bom_qty / product_yield',
        f'{p} × {b} / {_qty(yf)}',
    )


def _unit_names(unit_ids: list[int | None]) -> dict[int, str]:
    ids = [uid for uid in unit_ids if uid is not None]
    if not ids:
        return {}
    return dict(Unit.objects.filter(pk__in=ids).values_list('id', 'name'))


def _convert_bom_to_stock(
    qty: Decimal,
    *,
    bom_unit_id: int | None,
    stock_unit_id: int | None,
    product_id: int,
) -> tuple[Decimal, dict | None, str | None]:
    """BOM line unit → product.unit. Missing conversion keeps qty and warns."""
    names = _unit_names([bom_unit_id, stock_unit_id])
    bom_name = names.get(bom_unit_id) if bom_unit_id else None
    stock_name = names.get(stock_unit_id) if stock_unit_id else None
    if (
        bom_unit_id is None
        or stock_unit_id is None
        or bom_unit_id == stock_unit_id
    ):
        return qty, None, None
    product = Product.objects.get(pk=product_id)
    try:
        converted = to_product_unit(qty, bom_unit_id, product)
    except StockValidationError:
        return qty, {
            'op': 'convert_uom',
            'skipped': True,
            'reason': 'missing_uom_conversion',
            'from': f'{_qty(qty)} {bom_name or bom_unit_id}',
            'to': f'{_qty(qty)} {stock_name or stock_unit_id}',
        }, 'missing_uom_conversion'
    return converted, {
        'op': 'convert_uom',
        'formula': 'bom_unit → stock_unit',
        'from': f'{_qty(qty)} {bom_name or bom_unit_id}',
        'to': f'{_qty(converted)} {stock_name or stock_unit_id}',
    }, None


def _stock_step(stock: dict, gross_before: Decimal) -> dict:
    if not stock['applied']:
        return {
            'op': 'stock_net',
            'skipped': True,
            'reason': 'consider_stock=false',
        }
    return {
        'op': 'stock_net',
        'formula': 'gross - available',
        'from': f"{_qty(gross_before)} - {_qty(stock['available'])}",
        'to': _qty(stock['gross']),
        'available': _qty(stock['available']),
        'on_hand': _qty(stock['on_hand']),
        'min_stock_held': _qty(stock['min_stock_held']),
    }


def _run_stamp(*, run: PlanRun, plan: Plan, line_count: int, stamp: dict | None) -> dict:
    actor = stamp or {}
    return {
        'v': 1,
        'what': 'explode',
        'actor_sub': actor.get('actor_sub'),
        'actor_name': actor.get('actor_name'),
        'actor_email': actor.get('actor_email'),
        'source_workstation_ip': actor.get('source_workstation_ip'),
        'at': run.started_at.isoformat() if run.started_at else None,
        'plan_id': plan.id,
        'run_id': run.id,
        'run_number': run.run_number,
        'driver': DRIVER_VERSION,
        'line_count': line_count,
    }


def _event_payload(stamp_json: dict, extra: dict | None = None) -> dict:
    payload = dict(stamp_json)
    if extra:
        payload.update(extra)
    return payload


@transaction.atomic
def run_explode(
    plan_id: int,
    *,
    actor_user_id: int | None = None,
    stamp: dict | None = None,
) -> PlanRun:
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
    lines = list(plan.lines.order_by('sort_order', 'id'))
    stamp_json = _run_stamp(
        run=run, plan=plan, line_count=len(lines), stamp=stamp,
    )
    run.stamp_json = stamp_json
    run.save(update_fields=['stamp_json'])
    PlanEvent.objects.create(
        plan=plan,
        event_type='run_started',
        payload_json=_event_payload(stamp_json),
        actor_user_id=actor_user_id,
    )

    try:
        with transaction.atomic():
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
            payload_json=_event_payload(stamp_json),
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
            payload_json=_event_payload(stamp_json, {'error': str(to_raise)}),
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
    version_number = None
    if version_id is not None:
        recipe_spec = recipe_adapter.get_recipe_version(version_id)
        process_loss = recipe_spec.process_loss
        version_number = recipe_spec.version_number

    product = product_adapter.get_product_spec(
        line.product_id,
        process_loss=process_loss,
    )

    net = line.quantity
    if process_loss <= 0:
        process_loss = Decimal('1')
    gross_before_stock = net / process_loss

    consider = _consider_stock(line, product.consider_stock_in_plan)
    stock = netting.apply_stock_netting(
        net_required=net,
        gross_required=gross_before_stock,
        process_loss=process_loss,
        product=product,
        plan_date=plan.plan_date,
        consider_stock=consider,
    )
    net, gross, on_hand = stock['net'], stock['gross'], stock['on_hand']

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
    batch_count = len(batch_grosses)
    stock_step = _stock_step(stock, gross_before_stock)

    for batch_number, batch_gross in enumerate(batch_grosses, start=1):
        batch_net = batch_gross * process_loss
        calc = {
            'v': 1,
            'kind': 'demand',
            'summary': (
                f'{_qty(batch_net)} net / {_qty(batch_gross)} gross from line '
                f'{_qty(line.quantity)}. '
                + (
                    'Stock not applied.'
                    if not stock['applied']
                    else f"Stock available {_qty(stock['available'])}."
                )
            ),
            'inputs': {
                'plan_line_qty': _qty(line.quantity),
                'process_loss': _qty(process_loss),
                'recipe_yield_pct': _recipe_yield_pct(process_loss),
                'recipe_version_id': version_id,
                'recipe_version_number': version_number,
                'source_recipe_version_id': version_id,
                'source_recipe_version_number': version_number,
                'consider_stock': consider,
                'full_batches': full,
                'align_last_batch': align,
            },
            'steps': [
                _gross_step(line.quantity, process_loss, gross_before_stock),
                stock_step,
                {
                    'op': 'batch_split',
                    'formula': 'split gross into batches',
                    'from': _qty(gross),
                    'to': _qty(batch_gross),
                    'batch_number': batch_number,
                    'batch_count': batch_count,
                },
            ],
            'result': {
                'net': _qty(batch_net),
                'gross': _qty(batch_gross),
                'batch_number': batch_number,
                'batch_count': batch_count,
            },
        }
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
            calc_json=calc,
        )
        if recipe_spec is not None and batch_gross > 0:
            _explode_children(
                plan=plan,
                run=run,
                parent=parent,
                parent_gross=batch_gross,
                parent_product_id=product.id,
                parent_product_name=product.name,
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
    parent_product_id: int,
    parent_product_name: str,
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
        child_version_number = None
        if child_version_id is not None:
            child_recipe = recipe_adapter.get_recipe_version(child_version_id)
            child_loss = child_recipe.process_loss
            child_version_number = child_recipe.version_number

        child_product = product_adapter.get_product_spec(
            component.product_id,
            process_loss=child_loss,
        )
        if child_product.yield_factor <= 0:
            raise PlanningError(
                f'yield_factor must be > 0 for product {child_product.id}'
            )

        denom = batch_scale_denom(
            component.quantity,
            batch_quantity=recipe_spec.batch_quantity,
            bom_sum=bom_sum,
            process_batch=recipe_spec.process_batch,
        )
        net_scaled = scaled_child_net(
            parent_gross,
            component.quantity,
            yield_factor=child_product.yield_factor,
            batch_quantity=recipe_spec.batch_quantity,
            bom_sum=bom_sum,
            process_batch=recipe_spec.process_batch,
        )
        net_in_stock, uom_step, uom_warning = _convert_bom_to_stock(
            net_scaled,
            bom_unit_id=component.unit_id,
            stock_unit_id=child_product.unit_id,
            product_id=child_product.id,
        )
        if child_loss <= 0:
            child_loss = Decimal('1')
        gross_before_stock = net_in_stock / child_loss

        consider = _consider_stock(line, child_product.consider_stock_in_plan)
        stock = netting.apply_stock_netting(
            net_required=net_in_stock,
            gross_required=gross_before_stock,
            process_loss=child_loss,
            product=child_product,
            plan_date=plan.plan_date,
            consider_stock=consider,
        )
        net_child, gross_child, on_hand = (
            stock['net'], stock['gross'], stock['on_hand'],
        )

        full = _full_batches(line, child_product.full_batches_only)
        align = _align_last(line, child_product.align_unitary_weight)
        # Children inherit parent batch_number; do not re-split across new numbers
        # unless full_batches would change qty — apply split only as qty adjust on
        # same batch_number for MVP simplicity: one row per component per parent batch.
        batch_step = None
        gross_before_batch = gross_child
        if full and child_product.standard_batch_kg:
            parts = batching.split_batches(
                gross_child,
                full_batches_only=True,
                standard_batch_kg=child_product.standard_batch_kg,
                align_unitary_weight=align,
            )
            gross_child = sum(parts, Decimal('0'))
            net_child = gross_child * child_loss
            batch_step = {
                'op': 'full_batches',
                'formula': 'round gross up to standard batches',
                'from': _qty(gross_before_batch),
                'to': _qty(gross_child),
            }

        yf = child_product.yield_factor
        scale_formula, scale_from = _scale_bom_labels(
            parent_gross, component.quantity, denom, yf,
        )
        stock_note = (
            'Stock not applied.'
            if not stock['applied']
            else f"Stock available {_qty(stock['available'])}."
        )
        steps = [
            {
                'op': 'scale_bom',
                'formula': scale_formula,
                'from': scale_from,
                'to': _qty(net_scaled),
            },
        ]
        if uom_step is not None:
            steps.append(uom_step)
        steps.extend([
            _gross_step(net_in_stock, child_loss, gross_before_stock),
            _stock_step(stock, gross_before_stock),
        ])
        if batch_step is not None:
            steps.append(batch_step)

        names = _unit_names([component.unit_id, child_product.unit_id])
        calc = {
            'v': 1,
            'kind': 'child',
            'summary': (
                f'{_qty(net_child)} from parent {_qty(parent_gross)} × BOM '
                f'{_qty(component.quantity)}'
                + (f' / batch_quantity {_qty(denom)}' if denom else '')
                + (
                    ''
                    if _is_one(yf)
                    else f' / product_yield {_qty(yf)}'
                )
                + f'. {stock_note}'
            ),
            'inputs': {
                'parent_gross': _qty(parent_gross),
                'parent_product_id': parent_product_id,
                'parent_product_name': parent_product_name,
                'bom_qty': _qty(component.quantity),
                'bom_sum': _qty(bom_sum),
                'batch_quantity': _qty(denom or recipe_spec.batch_quantity),
                'yield_factor': _qty(yf),
                'process_loss': _qty(child_loss),
                'recipe_yield_pct': _recipe_yield_pct(child_loss),
                'recipe_version_id': child_version_id,
                'recipe_version_number': child_version_number,
                'source_recipe_version_id': recipe_version_id,
                'source_recipe_version_number': recipe_spec.version_number,
                'consider_stock': consider,
                'bom_unit': names.get(component.unit_id),
                'stock_unit': names.get(child_product.unit_id),
            },
            'steps': steps,
            'result': {
                'net': _qty(net_child),
                'gross': _qty(gross_child),
            },
        }
        if uom_warning:
            calc['warnings'] = [uom_warning]
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
            calc_json=calc,
        )

        if child_recipe is not None and gross_child > 0:
            _explode_children(
                plan=plan,
                run=run,
                parent=req,
                parent_gross=gross_child,
                parent_product_id=child_product.id,
                parent_product_name=child_product.name,
                recipe_version_id=child_version_id,
                depth=depth + 1,
                ancestry={*ancestry, child_product.id},
                batch_number=batch_number,
                line=line,
            )
