"""Chain-net: backward stock netting (chunks 1–8) with batch anomaly detection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from locations.models import Location
from product.models import ProductCosting, ProductSupplier, Unit

from planning.adapters import product as product_adapter
from planning.adapters import recipe as recipe_adapter
from planning.adapters import stock as stock_adapter
from planning.models import Plan, PlanLine
from planning.services.exceptions import PlanningError
from recipe.utils import scaled_child_net

MAX_BOM_DEPTH = 20


@dataclass
class BatchAnomaly:
    """Structured anomaly when min batch causes over/under production."""

    product_id: int
    product_name: str
    anomaly_type: Literal['over_production', 'cascade_increase', 'planner_decision']
    required_qty: Decimal
    actual_qty: Decimal
    delta_qty: Decimal
    min_batch: Decimal | None
    explanation: str
    affects_parent: bool = False
    affected_by_child: int | None = None
    action_required: bool = True
    suggested_actions: list[str] = field(default_factory=list)


@dataclass
class CascadeContext:
    """Track cascade effects through BOM levels."""

    anomalies: list[BatchAnomaly] = field(default_factory=list)
    child_overages: dict[int, Decimal] = field(default_factory=dict)
    parent_increases: dict[int, Decimal] = field(default_factory=dict)


def _dec(value: Decimal) -> str:
    return str(value)


def _stage_location_ids(
    *,
    source_id: int | None,
    destination_id: int | None,
) -> list[int]:
    """Stage ATP: destination ∪ source (finished WIP sits at dest after make)."""
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


def _stock_ref(*, lot_id: int, location_id: int) -> str:
    """Stable id for FE deep-links (lot@location balance identity)."""
    return f'lot:{lot_id}@location:{location_id}'


def _apply_min_batch(
    shortfall: Decimal,
    batch_quantity: Decimal | None,
) -> tuple[Decimal, Decimal | None, Decimal]:
    """Ceil shortfall to recipe batch_quantity.

    Returns (to_make, min_batch_or_none, over_production).
    """
    if shortfall <= 0:
        return Decimal('0'), None, Decimal('0')
    if batch_quantity is None or batch_quantity <= 0:
        return shortfall, None, Decimal('0')
    n = (shortfall / batch_quantity).to_integral_value(rounding=ROUND_CEILING)
    to_make = n * batch_quantity
    over_production = to_make - shortfall
    return to_make, batch_quantity, over_production


def _stock_lots_payload(
    product_id: int,
    location_ids: list[int],
) -> tuple[Decimal, list[dict], list[dict]]:
    names = _location_names(location_ids)
    lots = stock_adapter.lots_for_product(product_id, location_ids)
    stock = Decimal('0')
    rows: list[dict] = []
    by_loc: dict[int, Decimal] = defaultdict(lambda: Decimal('0'))

    for lot in lots:
        if lot.atp <= 0:
            continue
        stock += lot.atp
        by_loc[lot.location_id] += lot.atp
        rows.append({
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
            'stock_ref': _stock_ref(
                lot_id=lot.lot_id,
                location_id=lot.location_id,
            ),
            'balances_query': (
                f'/stock/balances/?product_id={product_id}'
                f'&lot_id={lot.lot_id}&location_id={lot.location_id}'
            ),
        })

    stock_by_location = [
        {
            'location_id': loc_id,
            'location_name': names.get(loc_id),
            'quantity': _dec(qty),
        }
        for loc_id, qty in by_loc.items()
    ]
    return stock, rows, stock_by_location


def _format_stock_cover(stock_lots: list[dict]) -> str:
    if not stock_lots:
        return ''
    parts: list[str] = []
    for row in stock_lots:
        loc = row.get('location_name') or f"location {row['location_id']}"
        trace = row.get('trace_number') or 'no-trace'
        ref = row.get('stock_ref') or ''
        parts.append(
            f"{row['atp']} at {loc} (trace {trace}, {ref})"
        )
    return '; '.join(parts)


def _explanation(
    *,
    demand: Decimal,
    stock: Decimal,
    shortfall: Decimal,
    to_make: Decimal,
    min_batch: Decimal | None,
    stock_lots: list[dict],
) -> str:
    if stock <= 0:
        base = (
            f'Demand {_dec(demand)}. No eligible stock at stage. '
            f'Shortfall {_dec(shortfall)}.'
        )
    else:
        cover = _format_stock_cover(stock_lots)
        applied = min(demand, stock)
        base = (
            f'Demand {_dec(demand)}. Covered by stock: {_dec(applied)} '
            f'({cover}). Shortfall {_dec(shortfall)}.'
        )
    if min_batch is not None and to_make > shortfall:
        return (
            f'{base} Min batch {_dec(min_batch)} → to make {_dec(to_make)}.'
        )
    return f'{base} To make: {_dec(to_make)}.'


def _line_summary(root: dict) -> str:
    applied = root['stock_applied']
    to_make = root['to_make']
    if Decimal(applied) <= 0:
        return f'No stage stock on finished good; planning {to_make}.'
    cover = _format_stock_cover(root.get('stock_lots') or [])
    return f'Using {applied} already on hand ({cover}); planning {to_make}.'


def net_node(
    *,
    product_id: int,
    demand: Decimal,
    is_top_fg: bool,
    recipe_version_id: int | None = None,
    depth: int = 0,
    ancestry: frozenset[int] | None = None,
    cascade_ctx: CascadeContext | None = None,
) -> dict:
    """Demand − stage ATP → to_make (min-batch ceil); recurse BOM when to_make > 0.

    With cascade_ctx, tracks batch anomalies and propagates over-production forward.
    """
    if depth > MAX_BOM_DEPTH:
        raise PlanningError(f'BOM depth exceeded MAX_BOM_DEPTH={MAX_BOM_DEPTH}')

    seen = ancestry or frozenset()
    if product_id in seen:
        raise PlanningError(f'BOM cycle detected at product {product_id}')

    ctx = cascade_ctx or CascadeContext()

    version_id = recipe_adapter.resolve_recipe_version_id(
        product_id,
        recipe_version_id,
    )
    process_loss = Decimal('1')
    recipe_spec = None
    batch_quantity = None
    if version_id is not None:
        recipe_spec = recipe_adapter.get_recipe_version(version_id)
        process_loss = recipe_spec.process_loss or Decimal('1')
        if process_loss <= 0:
            process_loss = Decimal('1')
        batch_quantity = recipe_spec.batch_quantity

    product = product_adapter.get_product_spec(
        product_id,
        process_loss=process_loss,
    )
    location_ids = _stage_location_ids(
        source_id=product.source_location_id,
        destination_id=product.destination_location_id,
    )
    stock, stock_lots, stock_by_location = _stock_lots_payload(
        product.id,
        location_ids,
    )
    shortfall = max(demand - stock, Decimal('0'))
    to_make, min_batch, over_production = _apply_min_batch(shortfall, batch_quantity)

    over_production_from_children = Decimal('0')
    children: list[dict] = []
    if recipe_spec is not None and to_make > 0:
        child_ancestry = seen | {product_id}
        for component in recipe_spec.components:
            child_product = product_adapter.get_product_spec(component.product_id)
            if child_product.yield_factor <= 0:
                raise PlanningError(
                    f'yield_factor must be > 0 for product {child_product.id}'
                )
            child_demand = to_make * component.quantity / child_product.yield_factor
            child_node = net_node(
                product_id=component.product_id,
                demand=child_demand,
                is_top_fg=False,
                recipe_version_id=None,
                depth=depth + 1,
                ancestry=child_ancestry,
                cascade_ctx=ctx,
            )
            children.append(child_node)

            child_over = ctx.child_overages.get(component.product_id, Decimal('0'))
            if child_over > 0 and component.quantity > 0:
                extra_parent_units = (
                    child_over * child_product.yield_factor / component.quantity
                )
                over_production_from_children += extra_parent_units

    if over_production > 0:
        ctx.child_overages[product_id] = over_production
        suggested = [
            f'Complete full batch of {_dec(to_make)} (recommended)',
            f'Adjust parent order to use extra {_dec(over_production)}',
        ]
        ctx.anomalies.append(BatchAnomaly(
            product_id=product_id,
            product_name=product.name,
            anomaly_type='over_production',
            required_qty=shortfall,
            actual_qty=to_make,
            delta_qty=over_production,
            min_batch=min_batch,
            explanation=(
                f'{product.name}: need {_dec(shortfall)}, min batch {_dec(min_batch)} '
                f'→ must make {_dec(to_make)} (over by {_dec(over_production)}). '
                f'Cannot make partial batch.'
            ),
            affects_parent=not is_top_fg,
            action_required=True,
            suggested_actions=suggested,
        ))

    if over_production_from_children > 0:
        ctx.parent_increases[product_id] = over_production_from_children
        suggested = [
            f'Increase {product.name} production to use child over-production',
            'Leave as-is (child over-production goes to stock)',
        ]
        ctx.anomalies.append(BatchAnomaly(
            product_id=product_id,
            product_name=product.name,
            anomaly_type='cascade_increase',
            required_qty=demand,
            actual_qty=demand + over_production_from_children,
            delta_qty=over_production_from_children,
            min_batch=min_batch,
            explanation=(
                f'{product.name}: child over-production enables making '
                f'{_dec(over_production_from_children)} extra. Consider completing full batch.'
            ),
            affects_parent=not is_top_fg,
            action_required=False,
            suggested_actions=suggested,
        ))

    return {
        'product_id': product.id,
        'product_name': product.name,
        'unit_id': product.unit_id,
        'recipe_version_id': version_id,
        'has_recipe': version_id is not None,
        'demand': _dec(demand),
        'stock': _dec(stock),
        'stock_applied': _dec(min(demand, stock)),
        'shortfall': _dec(shortfall),
        'min_batch': _dec(min_batch) if min_batch is not None else None,
        'to_make': _dec(to_make),
        'over_production': _dec(over_production) if over_production > 0 else None,
        'stage_location_ids': location_ids,
        'stock_lots': stock_lots,
        'stock_by_location': stock_by_location,
        'explanation': _explanation(
            demand=demand,
            stock=stock,
            shortfall=shortfall,
            to_make=to_make,
            min_batch=min_batch,
            stock_lots=stock_lots,
        ),
        'children': children,
    }


def _optional_qty(value, field_name: str) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        qty = Decimal(str(value))
    except Exception as exc:
        raise PlanningError(f'Invalid decimal for {field_name}') from exc
    if qty < 0:
        raise PlanningError(f'{field_name} must be >= 0')
    return qty


def _normalize_demand_inputs(raw: dict | None) -> dict:
    """Chunk 8 inputs. demand_source stub: manual | sales_order (future)."""
    raw = raw or {}
    source = raw.get('demand_source') or 'manual'
    if source not in ('manual', 'sales_order'):
        raise PlanningError("demand_source must be 'manual' or 'sales_order'")
    return {
        'demand_source': source,
        'manual_make_qty': _optional_qty(
            raw.get('manual_make_qty'), 'manual_make_qty',
        ),
        'today_pending_dispatch_qty': _optional_qty(
            raw.get('today_pending_dispatch_qty'),
            'today_pending_dispatch_qty',
        ) or Decimal('0'),
        'wip_fg_equivalent_qty': _optional_qty(
            raw.get('wip_fg_equivalent_qty'), 'wip_fg_equivalent_qty',
        ) or Decimal('0'),
    }


def _explode_children(
    *,
    product_id: int,
    to_make: Decimal,
    version_id: int | None,
    ancestry: frozenset[int],
    depth: int,
    cascade_ctx: CascadeContext | None = None,
) -> list[dict]:
    if version_id is None or to_make <= 0:
        return []
    recipe_spec = recipe_adapter.get_recipe_version(version_id)
    bom_sum = sum((c.quantity for c in recipe_spec.components), Decimal('0'))
    children: list[dict] = []
    child_ancestry = ancestry | {product_id}
    for component in recipe_spec.components:
        child_product = product_adapter.get_product_spec(component.product_id)
        if child_product.yield_factor <= 0:
            raise PlanningError(
                f'yield_factor must be > 0 for product {child_product.id}'
            )
        child_demand = scaled_child_net(
            to_make,
            component.quantity,
            yield_factor=child_product.yield_factor,
            batch_quantity=recipe_spec.batch_quantity,
            bom_sum=bom_sum,
            process_batch=recipe_spec.process_batch,
            parent_recipe_code=recipe_spec.recipe_code,
        )
        children.append(
            net_node(
                product_id=component.product_id,
                demand=child_demand,
                is_top_fg=False,
                recipe_version_id=None,
                depth=depth + 1,
                ancestry=child_ancestry,
                cascade_ctx=cascade_ctx,
            )
        )
    return children


def net_fg_line(
    line: PlanLine,
    demand_inputs: dict | None = None,
    cascade_ctx: CascadeContext | None = None,
) -> dict:
    """FG root with chunk-8 demand composition, then BOM children."""
    inputs = _normalize_demand_inputs(demand_inputs)
    plan_qty = line.quantity
    target = (
        inputs['manual_make_qty']
        if inputs['manual_make_qty'] is not None
        else plan_qty
    )
    pending = inputs['today_pending_dispatch_qty']
    wip = inputs['wip_fg_equivalent_qty']

    ctx = cascade_ctx or CascadeContext()

    version_id = recipe_adapter.resolve_recipe_version_id(
        line.product_id,
        line.recipe_version_id,
    )
    process_loss = Decimal('1')
    batch_quantity = None
    if version_id is not None:
        recipe_spec = recipe_adapter.get_recipe_version(version_id)
        process_loss = recipe_spec.process_loss or Decimal('1')
        if process_loss <= 0:
            process_loss = Decimal('1')
        batch_quantity = recipe_spec.batch_quantity

    product = product_adapter.get_product_spec(
        line.product_id,
        process_loss=process_loss,
    )
    location_ids = _stage_location_ids(
        source_id=product.source_location_id,
        destination_id=product.destination_location_id,
    )
    dispatch_stock, stock_lots, stock_by_location = _stock_lots_payload(
        product.id,
        location_ids,
    )
    free_dispatch = max(dispatch_stock - pending, Decimal('0'))
    cover = free_dispatch + wip
    shortfall = max(target - cover, Decimal('0'))
    to_make, min_batch, over_production = _apply_min_batch(shortfall, batch_quantity)

    children = _explode_children(
        product_id=product.id,
        to_make=to_make,
        version_id=version_id,
        ancestry=frozenset(),
        depth=0,
        cascade_ctx=ctx,
    )

    over_from_children = Decimal('0')
    if version_id is not None:
        recipe_spec_check = recipe_adapter.get_recipe_version(version_id)
        for component in recipe_spec_check.components:
            child_over = ctx.child_overages.get(component.product_id, Decimal('0'))
            if child_over > 0:
                child_product = product_adapter.get_product_spec(component.product_id)
                if child_product.yield_factor > 0 and component.quantity > 0:
                    extra_fg = child_over * child_product.yield_factor / component.quantity
                    over_from_children += extra_fg

    if over_production > 0:
        ctx.child_overages[product.id] = over_production
        suggested = [
            f'Complete full batch of {_dec(to_make)} (recommended)',
        ]
        ctx.anomalies.append(BatchAnomaly(
            product_id=product.id,
            product_name=product.name,
            anomaly_type='over_production',
            required_qty=shortfall,
            actual_qty=to_make,
            delta_qty=over_production,
            min_batch=min_batch,
            explanation=(
                f'{product.name}: need {_dec(shortfall)}, min batch {_dec(min_batch)} '
                f'→ must make {_dec(to_make)} (over by {_dec(over_production)}). '
                f'Cannot make partial batch.'
            ),
            affects_parent=False,
            action_required=True,
            suggested_actions=suggested,
        ))

    if over_from_children > 0:
        ctx.parent_increases[product.id] = over_from_children
        suggested = [
            f'Increase FG production by {_dec(over_from_children)} to use child over-production',
            'Leave as-is (child over-production goes to WIP stock)',
        ]
        ctx.anomalies.append(BatchAnomaly(
            product_id=product.id,
            product_name=product.name,
            anomaly_type='cascade_increase',
            required_qty=target,
            actual_qty=target + over_from_children,
            delta_qty=over_from_children,
            min_batch=min_batch,
            explanation=(
                f'{product.name}: child components over-produced. '
                f'Could make {_dec(over_from_children)} extra FG units.'
            ),
            affects_parent=False,
            action_required=False,
            suggested_actions=suggested,
        ))

    breakdown = {
        'demand_source': inputs['demand_source'],
        'plan_qty': _dec(plan_qty),
        'manual_make_qty': (
            _dec(inputs['manual_make_qty'])
            if inputs['manual_make_qty'] is not None
            else None
        ),
        'target': _dec(target),
        'dispatch_stock': _dec(dispatch_stock),
        'today_pending_dispatch_qty': _dec(pending),
        'free_dispatch': _dec(free_dispatch),
        'wip_fg_equivalent_qty': _dec(wip),
        'cover': _dec(cover),
    }
    explanation = (
        f"Target {_dec(target)} (plan {_dec(plan_qty)}"
        + (
            f", manual {_dec(inputs['manual_make_qty'])}"
            if inputs['manual_make_qty'] is not None
            else ''
        )
        + f"). Dispatch stock {_dec(dispatch_stock)}; "
        f"pending despatch {_dec(pending)} → free {_dec(free_dispatch)}; "
        f"WIP {_dec(wip)}; cover {_dec(cover)}. Shortfall {_dec(shortfall)}."
    )
    if min_batch is not None and to_make > shortfall:
        explanation += (
            f" Min batch {_dec(min_batch)} → to make {_dec(to_make)}."
        )
    else:
        explanation += f" To make: {_dec(to_make)}."

    node = {
        'product_id': product.id,
        'product_name': product.name,
        'unit_id': product.unit_id,
        'recipe_version_id': version_id,
        'has_recipe': version_id is not None,
        'demand': _dec(target),
        'stock': _dec(dispatch_stock),
        'stock_applied': _dec(min(target, cover)),
        'shortfall': _dec(shortfall),
        'min_batch': _dec(min_batch) if min_batch is not None else None,
        'to_make': _dec(to_make),
        'over_production': _dec(over_production) if over_production > 0 else None,
        'stage_location_ids': location_ids,
        'stock_lots': stock_lots,
        'stock_by_location': stock_by_location,
        'demand_breakdown': breakdown,
        'explanation': explanation,
        'children': children,
        'plan_line_id': line.id,
    }
    node['summary'] = (
        f"Target {breakdown['target']}; cover {breakdown['cover']} "
        f"(free despatch {breakdown['free_dispatch']} + WIP {breakdown['wip_fg_equivalent_qty']}); "
        f"planning {node['to_make']}."
    )
    return node


def _walk_nodes(node: dict):
    yield node
    for child in node.get('children') or []:
        yield from _walk_nodes(child)


def _supplier_defaults(product_ids: list[int]) -> dict[int, dict]:
    if not product_ids:
        return {}
    rows = (
        ProductSupplier.objects
        .filter(product_id__in=product_ids, is_active=True)
        .select_related('supplier')
        .order_by('product_id', '-is_default', 'id')
    )
    out: dict[int, dict] = {}
    for row in rows:
        if row.product_id in out:
            continue
        out[row.product_id] = {
            'supplier_id': row.supplier_id,
            'supplier_name': row.supplier.name if row.supplier_id else None,
            'supplier_code': row.supplier_code,
            'supplier_product_name': row.supplier_product_name,
        }
    return out


def _unit_names(unit_ids: list[int]) -> dict[int, str]:
    if not unit_ids:
        return {}
    return dict(Unit.objects.filter(pk__in=unit_ids).values_list('id', 'name'))


def _unit_costs(product_ids: list[int]) -> dict[int, Decimal]:
    if not product_ids:
        return {}
    return {
        row.product_id: row.unit_cost
        for row in ProductCosting.objects.filter(product_id__in=product_ids)
    }


def build_tab_views(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split chain-net trees into Product Line Planning + Ingredient Plan rows."""
    recipe_nodes: list[dict] = []
    material_acc: dict[int, dict] = {}

    for root in items:
        plan_line_id = root.get('plan_line_id')
        for node in _walk_nodes(root):
            pid = node['product_id']
            demand = Decimal(node['demand'])
            to_make = Decimal(node['to_make'])
            stock = Decimal(node['stock'])
            if node.get('has_recipe'):
                recipe_nodes.append({
                    'plan_line_id': plan_line_id,
                    'product_id': pid,
                    'product_name': node['product_name'],
                    'unit_id': node.get('unit_id'),
                    'recipe_version_id': node.get('recipe_version_id'),
                    'demand': node['demand'],
                    'stock': node['stock'],
                    'shortfall': node['shortfall'],
                    'min_batch': node.get('min_batch'),
                    'to_make': node['to_make'],
                    'explanation': node.get('explanation'),
                    'stock_lots': list(node.get('stock_lots') or []),
                    'stock_by_location': list(node.get('stock_by_location') or []),
                })
                continue
            if demand <= 0 and to_make <= 0:
                continue
            acc = material_acc.get(pid)
            if acc is None:
                material_acc[pid] = {
                    'product_id': pid,
                    'product_name': node['product_name'],
                    'unit_id': node.get('unit_id'),
                    'quantity': demand,
                    'stock': stock,
                    'stock_lots': list(node.get('stock_lots') or []),
                }
            else:
                acc['quantity'] += demand
                if stock > acc['stock']:
                    acc['stock'] = stock
                    acc['stock_lots'] = list(node.get('stock_lots') or [])

    unit_ids = [
        n['unit_id'] for n in recipe_nodes if n.get('unit_id') is not None
    ] + [
        m['unit_id'] for m in material_acc.values() if m.get('unit_id') is not None
    ]
    names = _unit_names(list({u for u in unit_ids if u is not None}))
    product_ids = list(material_acc.keys())
    costs = _unit_costs(product_ids)
    suppliers = _supplier_defaults(product_ids)

    product_lines = []
    for row in recipe_nodes:
        uid = row.get('unit_id')
        product_lines.append({
            **row,
            'unit_name': names.get(uid) if uid is not None else None,
        })

    ingredients = []
    for pid, acc in sorted(
        material_acc.items(),
        key=lambda kv: (kv[1]['product_name'] or '', kv[0]),
    ):
        qty = acc['quantity']
        stock = acc['stock']
        balance = stock - qty
        unit_cost = costs.get(pid)
        material_cost = (unit_cost * qty) if unit_cost is not None else None
        uid = acc.get('unit_id')
        status = 'ok' if balance >= 0 else 'short'
        ingredients.append({
            'product_id': pid,
            'product_name': acc['product_name'],
            'unit_id': uid,
            'unit_name': names.get(uid) if uid is not None else None,
            'quantity': _dec(qty),
            'stock': _dec(stock),
            'balance': _dec(balance),
            'stock_status': status,
            'unit_cost': _dec(unit_cost) if unit_cost is not None else None,
            'material_cost': _dec(material_cost) if material_cost is not None else None,
            'supplier': suppliers.get(pid),
            'stock_lots': acc.get('stock_lots') or [],
        })

    return product_lines, ingredients


def _anomaly_to_dict(a: BatchAnomaly) -> dict:
    """Serialize BatchAnomaly for JSON response."""
    return {
        'product_id': a.product_id,
        'product_name': a.product_name,
        'anomaly_type': a.anomaly_type,
        'required_qty': _dec(a.required_qty),
        'actual_qty': _dec(a.actual_qty),
        'delta_qty': _dec(a.delta_qty),
        'min_batch': _dec(a.min_batch) if a.min_batch else None,
        'explanation': a.explanation,
        'affects_parent': a.affects_parent,
        'affected_by_child': a.affected_by_child,
        'action_required': a.action_required,
        'suggested_actions': a.suggested_actions,
    }


def chain_net_plan(
    plan_id: int,
    *,
    line_ids: list[int] | None = None,
    demand_inputs: dict | None = None,
    line_demand: dict[int, dict] | None = None,
) -> dict:
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

    ctx = CascadeContext()
    line_demand = line_demand or {}
    items = []
    for line in lines:
        per_line = line_demand.get(line.id) or line_demand.get(str(line.id))
        merged = {**(demand_inputs or {}), **(per_line or {})}
        items.append(net_fg_line(line, demand_inputs=merged or None, cascade_ctx=ctx))
    product_lines, ingredients = build_tab_views(items)

    batch_anomalies = [_anomaly_to_dict(a) for a in ctx.anomalies]

    return {
        'plan_id': plan.id,
        'plan_date': plan.plan_date.isoformat(),
        'items': items,
        'product_lines': product_lines,
        'ingredients': ingredients,
        'batch_anomalies': batch_anomalies,
        'has_batch_anomalies': len(batch_anomalies) > 0,
    }
