"""Chunk 1: FG demand minus stage stock → to_make (+ lot traces).

ponytail: no BOM / min-batch / HTTP yet — chunks 2–5.
"""

from __future__ import annotations

from decimal import Decimal

from locations.models import Location

from planning.adapters import product as product_adapter
from planning.adapters import stock as stock_adapter
from planning.models import Plan, PlanLine
from planning.services.exceptions import PlanningError


def _dec(value: Decimal) -> str:
    return str(value)


def _fg_stage_location_ids(
    *,
    source_id: int | None,
    destination_id: int | None,
) -> list[int]:
    """Top FG: stock at destination ∪ source (e.g. Dispatch + make site)."""
    ids: list[int] = []
    for loc_id in (destination_id, source_id):
        if loc_id is not None and loc_id not in ids:
            ids.append(loc_id)
    return ids


def _location_names(location_ids: list[int]) -> dict[int, str]:
    if not location_ids:
        return {}
    return dict(
        Location.objects.filter(pk__in=location_ids).values_list('id', 'name')
    )


def net_fg_line(line: PlanLine) -> dict:
    """One plan line (finished good only): demand − stage ATP → to_make."""
    product = product_adapter.get_product_spec(line.product_id)
    location_ids = _fg_stage_location_ids(
        source_id=product.source_location_id,
        destination_id=product.destination_location_id,
    )
    names = _location_names(location_ids)
    lots = stock_adapter.lots_for_product(product.id, location_ids)

    stock = Decimal('0')
    stock_lots: list[dict] = []
    for lot in lots:
        if lot.atp <= 0:
            continue
        stock += lot.atp
        stock_lots.append({
            'lot_id': lot.lot_id,
            'trace_number': lot.trace_number,
            'location_id': lot.location_id,
            'location_name': names.get(lot.location_id),
            'quantity': _dec(lot.quantity_on_hand),
            'atp': _dec(lot.atp),
            'use_by': lot.use_by.isoformat() if lot.use_by else None,
            'production_date': (
                lot.production_date.isoformat() if lot.production_date else None
            ),
        })

    demand = line.quantity
    shortfall = max(demand - stock, Decimal('0'))
    return {
        'plan_line_id': line.id,
        'product_id': product.id,
        'product_name': product.name,
        'demand': _dec(demand),
        'stock': _dec(stock),
        'stock_applied': _dec(min(demand, stock)),
        'shortfall': _dec(shortfall),
        'to_make': _dec(shortfall),
        'stage_location_ids': location_ids,
        'stock_lots': stock_lots,
        'children': [],
    }


def chain_net_plan(plan_id: int, *, line_ids: list[int] | None = None) -> dict:
    """Chunk 1 entry: net each FG plan line (no BOM children)."""
    try:
        plan = Plan.objects.get(pk=plan_id)
    except Plan.DoesNotExist as exc:
        raise PlanningError(f'plan {plan_id} not found') from exc

    qs = plan.lines.order_by('sort_order', 'id')
    if line_ids is not None:
        qs = qs.filter(id__in=line_ids)
    lines = list(qs)
    if not lines:
        raise PlanningError('plan has no demand lines')

    return {
        'plan_id': plan.id,
        'plan_date': plan.plan_date.isoformat(),
        'items': [net_fg_line(line) for line in lines],
    }
