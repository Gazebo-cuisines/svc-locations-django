"""Experimental batch-multiplier driver (read-only).

Mirrors how the Excel day-plan separates two figures per recipe:

- Raw materials scale on the EXACT yield-adjusted output
  (``demand / process_loss``), NOT on rounded batches. This matches the
  workbook's Fresh Products / Ingredients sheets, which use fractional
  ``Batches Required`` (verified: batch-rounding raw material over-purchases
  by ~15% on a real plan).

- The batch count is rounded for the PRODUCTION schedule only and reported
  separately (``production_batches`` / ``production_output``), never applied to
  ingredient quantities. Two regimes:
  - Cook / process (``batch_quantity`` set): whole batches on the raw recipe
    weight per batch, ``ceil((demand / process_loss) / batch_quantity)``.
  - Belt / folding (``One Mix Qty`` from ``ResourceProductRate.batch_size``):
    fractional batches to a fixed 0.25 increment,
    ``ceil(demand / one_mix_qty / 0.25) * 0.25``.

Nothing is written to the database — the driver returns a JSON tree plus a
raw-material rollup for diffing against the day-plan workbook.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from product.models import Product, Unit

from planning.adapters import product as product_adapter
from planning.adapters import recipe as recipe_adapter
from planning.models import (
    Plan,
    PlanLine,
    PlanRequirement,
    PlanRun,
    PlanRunStatus,
    Resource,
    ResourceProductRate,
)
from planning.services import chain_net, netting
from planning.services.exceptions import PlanningError
from recipe.utils import batch_scale_denom, scaled_child_net
from stock_ledger.util.conversions import StockValidationError, to_product_unit

DRIVER_VERSION = 'batchmult-0.2'
MAX_BOM_DEPTH = 20
DEFAULT_INCREMENT = Decimal('0.25')

REGIME_COOK = 'cook'
REGIME_BELT = 'belt'
REGIME_PER_UNIT = 'per_unit'


def _dec(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


_GRAM_UNITS = frozenset({'g', 'gram', 'grams', 'gm', 'gms'})
_KG_UNITS = frozenset({'kg', 'kgs', 'kilogram', 'kilograms'})
_MG_UNITS = frozenset({'mg', 'milligram', 'milligrams'})


def _unit_key(unit_name: str | None) -> str:
    return (unit_name or '').strip().lower()


def _to_kg(qty: Decimal, unit_name: str | None) -> Decimal | None:
    unit = _unit_key(unit_name)
    if unit in _GRAM_UNITS:
        return qty / Decimal('1000')
    if unit in _KG_UNITS:
        return qty
    if unit in _MG_UNITS:
        return qty / Decimal('1000000')
    return None


def _round_ingredient_qty(
    qty: Decimal, unit_name: str | None,
) -> tuple[Decimal, Decimal | None]:
    """Match Excel Fresh Products display: whole grams, kg to 2 dp."""
    unit = _unit_key(unit_name)
    if unit in _GRAM_UNITS:
        rounded = qty.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return rounded, rounded / Decimal('1000')
    kg = _to_kg(qty, unit_name)
    if kg is not None:
        kg = kg.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return qty, kg


class _Meta:
    """Lightweight per-product lookup cache (name / code / category / unit)."""

    def __init__(self) -> None:
        self._products: dict[int, Product] = {}
        self._one_mix: dict[int, Decimal | None] = {}
        self._unit_names: dict[int, str | None] = {}
        self._stock: dict[tuple, tuple[Decimal, list[dict], list[dict]]] = {}

    def product(self, product_id: int) -> Product:
        row = self._products.get(product_id)
        if row is None:
            row = (
                Product.objects
                .select_related('category', 'unit')
                .only('id', 'name', 'recipe_code', 'category__name', 'unit__name')
                .get(pk=product_id)
            )
            self._products[product_id] = row
        return row

    def unit_name(self, unit_id: int | None) -> str | None:
        if unit_id is None:
            return None
        if unit_id not in self._unit_names:
            row = Unit.objects.filter(pk=unit_id).values_list('name', flat=True).first()
            self._unit_names[unit_id] = row
        return self._unit_names[unit_id]

    def stock(
        self, product_id: int, location_ids: list[int],
    ) -> tuple[Decimal, list[dict], list[dict]]:
        """Stage ATP for the product. Display only — never nets the demand."""
        key = (product_id, tuple(location_ids))
        hit = self._stock.get(key)
        if hit is None:
            hit = chain_net._stock_lots_payload(product_id, location_ids)
            self._stock[key] = hit
        return hit

    def one_mix_qty(self, product_id: int) -> Decimal | None:
        """One Mix Qty for the product, ignoring resource / staff_count."""
        if product_id not in self._one_mix:
            value = (
                ResourceProductRate.objects
                .filter(product_id=product_id, is_active=True, batch_size__isnull=False)
                .order_by('-batch_size')
                .values_list('batch_size', flat=True)
                .first()
            )
            self._one_mix[product_id] = value
        return self._one_mix[product_id]


def _ceil(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_CEILING)


def _pick_regime(
    *,
    batch_quantity: Decimal | None,
    one_mix_qty: Decimal | None,
) -> str:
    if batch_quantity is not None and batch_quantity > 0:
        return REGIME_COOK
    if one_mix_qty is not None and one_mix_qty > 0:
        return REGIME_BELT
    return REGIME_PER_UNIT


def _production_batches(
    *,
    demand: Decimal,
    process_loss: Decimal,
    regime: str,
    batch_quantity: Decimal | None,
    one_mix_qty: Decimal | None,
    increment: Decimal,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Rounded production figures ONLY (batches, batch_yield, rounded_output).

    These are reported for the cook/belt schedule; raw materials are NOT scaled
    by them. The Excel day-plan keeps raw material on exact fractional batches
    and rounds only the production batch count.
    """
    if process_loss <= 0:
        process_loss = Decimal('1')
    if regime == REGIME_COOK and batch_quantity:
        raw_needed = demand / process_loss
        batches = _ceil(raw_needed / batch_quantity)
        return batches, batch_quantity, batches * batch_quantity
    if regime == REGIME_BELT and one_mix_qty:
        batches = _ceil((demand / one_mix_qty) / increment) * increment
        return batches, one_mix_qty, one_mix_qty * batches
    return None, None, None


def _convert_to_stock(
    qty: Decimal,
    *,
    bom_unit_id: int | None,
    stock_unit_id: int | None,
    product_id: int,
    meta: _Meta,
) -> tuple[Decimal, dict | None]:
    if (
        bom_unit_id is None
        or stock_unit_id is None
        or bom_unit_id == stock_unit_id
    ):
        return qty, None
    bom_name = meta.unit_name(bom_unit_id) or bom_unit_id
    stock_name = meta.unit_name(stock_unit_id) or stock_unit_id
    try:
        product = Product.objects.get(pk=product_id)
        converted = to_product_unit(qty, bom_unit_id, product)
    except StockValidationError:
        return qty, {
            'op': 'convert_uom',
            'skipped': True,
            'reason': 'missing_uom_conversion',
            'from': f'{_dec(qty)} {bom_name}',
            'to': f'{_dec(qty)} {stock_name}',
        }
    return converted, {
        'op': 'convert_uom',
        'formula': 'bom_unit → stock_unit',
        'from': f'{_dec(qty)} {bom_name}',
        'to': f'{_dec(converted)} {stock_name}',
    }


def _scale_step(
    *,
    parent_gross: Decimal,
    bom_qty: Decimal,
    denom: Decimal | None,
    yield_factor: Decimal,
    result: Decimal,
) -> dict:
    formula = ['parent_gross × bom_qty']
    values = [f'{_dec(parent_gross)} × {_dec(bom_qty)}']
    if denom:
        formula.append('batch_quantity')
        values.append(_dec(denom))
    if yield_factor != Decimal('1'):
        formula.append('product_yield')
        values.append(_dec(yield_factor))
    return {
        'op': 'scale_bom',
        'formula': ' / '.join(formula),
        'from': ' / '.join(values),
        'to': _dec(result),
    }


def _accumulate(
    rollup: dict[int, dict],
    *,
    product: Product,
    qty: Decimal,
    stock: Decimal,
    stock_lots: list[dict],
) -> None:
    acc = rollup.get(product.id)
    if acc is None:
        unit_name = product.unit.name if product.unit_id else None
        rollup[product.id] = {
            'product_id': product.id,
            'product_name': product.name,
            'recipe_code': product.recipe_code,
            'unit_id': product.unit_id,
            'unit_name': unit_name,
            'quantity': qty,
            'stock': stock,
            'stock_lots': list(stock_lots),
        }
        return
    acc['quantity'] += qty
    if stock > acc['stock']:
        acc['stock'] = stock
        acc['stock_lots'] = list(stock_lots)


def _node_stock(
    spec, product_id: int, meta: _Meta,
) -> tuple[Decimal, list[dict], list[dict]]:
    return meta.stock(
        product_id,
        chain_net._stage_location_ids(
            source_id=spec.source_location_id,
            destination_id=spec.destination_location_id,
        ),
    )


def _safe_resource_id(resource_id: int | None) -> int | None:
    if resource_id is None:
        return None
    if Resource.objects.filter(pk=resource_id).exists():
        return resource_id
    return None


def _net_node(
    *,
    product_id: int,
    demand: Decimal,
    depth: int,
    ancestry: frozenset[int],
    increment: Decimal,
    consider_stock: bool,
    plan_date,
    meta: _Meta,
    rollup: dict[int, dict],
    run: PlanRun | None = None,
    plan_line: PlanLine | None = None,
    parent: PlanRequirement | None = None,
    trace: list[dict] | None = None,
) -> dict:
    if depth > MAX_BOM_DEPTH:
        raise PlanningError(f'BOM depth exceeded MAX_BOM_DEPTH={MAX_BOM_DEPTH}')
    if product_id in ancestry:
        raise PlanningError(f'BOM cycle detected at product {product_id}')

    product = meta.product(product_id)
    version_id = recipe_adapter.resolve_recipe_version_id(product_id, None)
    recipe_spec = (
        recipe_adapter.get_recipe_version(version_id)
        if version_id is not None
        else None
    )

    # Leaf raw material: no recipe -> accumulate demand and stop.
    if recipe_spec is None:
        leaf_unit = meta.unit_name(product.unit_id)
        leaf_spec = product_adapter.get_product_spec(product_id)
        leaf_stock, leaf_lots, leaf_by_location = _node_stock(
            leaf_spec, product_id, meta,
        )
        _accumulate(
            rollup,
            product=product,
            qty=demand,
            stock=leaf_stock,
            stock_lots=leaf_lots,
        )
        if run is not None:
            PlanRequirement.objects.create(
                run=run,
                plan_line=plan_line,
                parent_requirement=parent,
                level=depth + 1,
                batch_number=1,
                product_id=product_id,
                recipe_version_id=None,
                net_required=demand,
                gross_required=demand,
                yield_factor=Decimal('1'),
                process_loss=Decimal('1'),
                balance=demand,
                closed=False,
                calc_json={
                    'v': 1,
                    'kind': 'material',
                    'driver': DRIVER_VERSION,
                    'summary': (
                        f'Raw material {_dec(demand)} {leaf_unit or ""}'.strip()
                        + ' — exact, never scaled by rounded batches.'
                    ),
                    'steps': list(trace or []),
                    'result': {'net': _dec(demand), 'gross': _dec(demand)},
                },
            )
        return {
            'product_id': product_id,
            'product_name': product.name,
            'recipe_code': product.recipe_code,
            'unit_id': product.unit_id,
            'unit_name': leaf_unit,
            'has_recipe': False,
            'demand': _dec(demand),
            'stock': _dec(leaf_stock),
            'shortfall': _dec(max(demand - leaf_stock, Decimal('0'))),
            'stock_lots': leaf_lots,
            'stock_by_location': leaf_by_location,
            'children': [],
        }

    spec = product_adapter.get_product_spec(product_id, process_loss=recipe_spec.process_loss)
    process_loss = recipe_spec.process_loss or Decimal('1')
    if process_loss <= 0:
        process_loss = Decimal('1')

    node_stock, node_lots, node_by_location = _node_stock(spec, product_id, meta)

    net_demand = demand
    stock_applied = Decimal('0')
    if consider_stock:
        stock = netting.apply_stock_netting(
            net_required=demand,
            gross_required=demand / process_loss,
            process_loss=process_loss,
            product=spec,
            plan_date=plan_date,
            consider_stock=True,
        )
        net_demand = stock['net']
        stock_applied = demand - net_demand

    one_mix = meta.one_mix_qty(product_id)
    regime = _pick_regime(
        batch_quantity=recipe_spec.batch_quantity,
        one_mix_qty=one_mix,
    )
    # Raw materials scale on the EXACT yield-adjusted output (matches Excel).
    effective = net_demand / process_loss
    # Rounded batch count is reported for production only, never for RM.
    batches, batch_yield, production_output = _production_batches(
        demand=net_demand,
        process_loss=process_loss,
        regime=regime,
        batch_quantity=recipe_spec.batch_quantity,
        one_mix_qty=one_mix,
        increment=increment,
    )

    bom_sum = sum((c.quantity for c in recipe_spec.components), Decimal('0'))
    denom = batch_scale_denom(
        Decimal('0'),
        batch_quantity=recipe_spec.batch_quantity,
        bom_sum=bom_sum,
        process_batch=recipe_spec.process_batch,
        parent_recipe_code=recipe_spec.recipe_code,
    )
    explanation = _explain(
        regime=regime,
        demand=net_demand,
        process_loss=process_loss,
        batch_yield=batch_yield,
        batches=batches,
        effective=effective,
        production_output=production_output,
        increment=increment,
    )

    steps = list(trace or [])
    steps.append(
        {
            'op': 'stock_net',
            'formula': 'demand - stock applied',
            'from': f'{_dec(demand)} - {_dec(stock_applied)}',
            'to': _dec(net_demand),
        }
        if consider_stock
        else {
            'op': 'stock_net',
            'skipped': True,
            'reason': 'consider_stock=false',
        }
    )
    steps.append({
        'op': 'gross',
        'formula': 'net / recipe_yield',
        'from': f'{_dec(net_demand)} / {_dec(process_loss)}',
        'to': _dec(effective),
    })
    steps.append(
        {
            'op': 'batch_plan',
            'formula': 'production schedule only — raw material stays exact',
            'from': (
                f'{_dec(effective if regime == REGIME_COOK else net_demand)}'
                f' / batch {_dec(batch_yield)}'
            ),
            'to': f'{_dec(batches)} batches = {_dec(production_output)}',
            'regime': regime,
        }
        if batches is not None
        else {
            'op': 'batch_plan',
            'skipped': True,
            'reason': f'{regime}: no batch size configured',
        }
    )

    # Persist this recipe node first so children can link it as parent.
    req = None
    if run is not None:
        yf_node = spec.yield_factor if spec.yield_factor > 0 else Decimal('1')
        req = PlanRequirement.objects.create(
            run=run,
            plan_line=plan_line,
            parent_requirement=parent,
            level=depth + 1,
            batch_number=1,
            product_id=product_id,
            recipe_version_id=version_id,
            net_required=net_demand,
            gross_required=effective,
            yield_factor=yf_node,
            process_loss=process_loss,
            source_location_id=spec.source_location_id,
            destination_location_id=spec.destination_location_id,
            default_resource_id=_safe_resource_id(spec.default_resource_id),
            stock_on_hand=stock_applied,
            balance=effective,
            closed=False,
            calc_json={
                'v': 1,
                'kind': 'recipe',
                'driver': DRIVER_VERSION,
                'regime': regime,
                'effective_output': _dec(effective),
                'batch_yield': _dec(batch_yield),
                'production_batches': _dec(batches),
                'production_output': _dec(production_output),
                'summary': explanation,
                'steps': steps,
                'result': {'net': _dec(net_demand), 'gross': _dec(effective)},
            },
        )

    child_ancestry = ancestry | {product_id}
    children: list[dict] = []
    for component in recipe_spec.components:
        child_product = product_adapter.get_product_spec(component.product_id)
        yf = child_product.yield_factor
        if yf <= 0:
            raise PlanningError(
                f'yield_factor must be > 0 for product {child_product.id}'
            )
        child_net = scaled_child_net(
            effective,
            component.quantity,
            yield_factor=yf,
            batch_quantity=recipe_spec.batch_quantity,
            bom_sum=bom_sum,
            process_batch=recipe_spec.process_batch,
            parent_recipe_code=recipe_spec.recipe_code,
        )
        child_trace = [
            _scale_step(
                parent_gross=effective,
                bom_qty=component.quantity,
                denom=denom,
                yield_factor=yf,
                result=child_net,
            )
        ]
        child_net, uom_step = _convert_to_stock(
            child_net,
            bom_unit_id=component.unit_id,
            stock_unit_id=child_product.unit_id,
            product_id=child_product.id,
            meta=meta,
        )
        if uom_step is not None:
            child_trace.append(uom_step)
        children.append(
            _net_node(
                product_id=component.product_id,
                demand=child_net,
                depth=depth + 1,
                ancestry=child_ancestry,
                increment=increment,
                consider_stock=consider_stock,
                plan_date=plan_date,
                meta=meta,
                rollup=rollup,
                run=run,
                plan_line=None,
                parent=req,
                trace=child_trace,
            )
        )

    return {
        'product_id': product_id,
        'product_name': product.name,
        'recipe_code': product.recipe_code,
        'unit_id': product.unit_id,
        'unit_name': meta.unit_name(product.unit_id),
        'has_recipe': True,
        'recipe_version_id': version_id,
        'regime': regime,
        'demand': _dec(demand),
        'net_demand': _dec(net_demand),
        'stock_applied': _dec(stock_applied),
        # Stock overlay is display only; it never reduces the requirement.
        'stock': _dec(node_stock),
        'shortfall': _dec(max(demand - node_stock, Decimal('0'))),
        'stock_lots': node_lots,
        'stock_by_location': node_by_location,
        'process_loss': _dec(process_loss),
        # Raw materials use this exact yield-adjusted output.
        'effective_output': _dec(effective),
        # Production schedule only (rounded); does NOT scale raw materials.
        'batch_yield': _dec(batch_yield),
        'production_batches': _dec(batches),
        'production_output': _dec(production_output),
        'scale_denominator': _dec(denom),
        'requirement_id': req.id if req is not None else None,
        'explanation': explanation,
        'children': children,
    }


def _explain(
    *,
    regime: str,
    demand: Decimal,
    process_loss: Decimal,
    batch_yield: Decimal | None,
    batches: Decimal | None,
    effective: Decimal,
    production_output: Decimal | None,
    increment: Decimal,
) -> str:
    if regime == REGIME_COOK and batch_yield:
        return (
            f'Cook: raw material on exact {_dec(effective)} '
            f'(= {_dec(demand)} / yield {_dec(process_loss)}). '
            f'Production: {_dec(effective)} / batch {_dec(batch_yield)} '
            f'-> {_dec(batches)} whole batches ({_dec(production_output)}).'
        )
    if regime == REGIME_BELT and batch_yield:
        return (
            f'Belt: raw material on exact {_dec(demand)} units. '
            f'Production: {_dec(demand)} / One Mix {_dec(batch_yield)} '
            f'rounded up to {_dec(increment)} -> {_dec(batches)} batches '
            f'({_dec(production_output)} units).'
        )
    return (
        f'Per-unit: no batch rounding; exact effective {_dec(effective)} '
        f'(= {_dec(demand)} / yield {_dec(process_loss)}).'
    )


def _build_ingredients(rollup: dict[int, dict]) -> list[dict]:
    product_ids = list(rollup.keys())
    suppliers = chain_net._supplier_defaults(product_ids)
    costs = chain_net._unit_costs(product_ids)

    out = []
    for pid, acc in sorted(
        rollup.items(),
        key=lambda kv: (kv[1]['product_name'] or '', kv[0]),
    ):
        qty, kg = _round_ingredient_qty(acc['quantity'], acc['unit_name'])
        stock = acc['stock']
        balance = stock - qty
        unit_cost = costs.get(pid)
        out.append({
            'product_id': pid,
            'product_name': acc['product_name'],
            'recipe_code': acc['recipe_code'],
            'unit_id': acc['unit_id'],
            'unit_name': acc['unit_name'],
            'quantity': _dec(qty),
            'kg': _dec(kg) if kg is not None else None,
            'stock': _dec(stock),
            'balance': _dec(balance),
            'stock_status': 'ok' if balance >= 0 else 'short',
            'unit_cost': _dec(unit_cost) if unit_cost is not None else None,
            'material_cost': _dec(unit_cost * qty) if unit_cost is not None else None,
            'supplier': suppliers.get(pid),
            'stock_lots': acc['stock_lots'],
        })
    return out


def _walk_nodes(node: dict):
    yield node
    for child in node.get('children') or []:
        yield from _walk_nodes(child)


def _build_product_lines(items: list[dict]) -> list[dict]:
    """Flatten recipe nodes for the 'To make' tab."""
    rows: list[dict] = []
    for root in items:
        plan_line_id = root.get('plan_line_id')
        for node in _walk_nodes(root):
            if not node.get('has_recipe'):
                continue
            rows.append({
                'plan_line_id': plan_line_id,
                'product_id': node['product_id'],
                'product_name': node['product_name'],
                'recipe_code': node.get('recipe_code'),
                'unit_id': node.get('unit_id'),
                'unit_name': node.get('unit_name'),
                'recipe_version_id': node.get('recipe_version_id'),
                'regime': node.get('regime'),
                'demand': node['demand'],
                'effective_output': node.get('effective_output'),
                'stock': node.get('stock'),
                'shortfall': node.get('shortfall'),
                'batch_yield': node.get('batch_yield'),
                'production_batches': node.get('production_batches'),
                'production_output': node.get('production_output'),
                'explanation': node.get('explanation'),
                'stock_lots': list(node.get('stock_lots') or []),
                'stock_by_location': list(node.get('stock_by_location') or []),
            })
    return rows


def _demand_breakdown(
    *, target: Decimal, stock: Decimal, pending: Decimal, wip: Decimal,
) -> dict:
    """Plan-B style composition. Display only — RM always uses the target."""
    free_dispatch = max(stock - pending, Decimal('0'))
    return {
        'target': _dec(target),
        'free_despatch': _dec(free_dispatch),
        'wip': _dec(wip),
        'cover': _dec(free_dispatch + wip),
    }


def _next_run_number(plan: Plan) -> int:
    last = (
        plan.runs.order_by('-run_number')
        .values_list('run_number', flat=True)
        .first()
    )
    return (last or 0) + 1


def run_batchmult_plan(
    plan_id: int,
    *,
    line_ids: list[int] | None = None,
    increment: Decimal | None = None,
    consider_stock: bool = False,
    persist: bool = False,
    actor_name: str | None = None,
    demand_inputs: dict | None = None,
    line_demand: dict[int, dict] | None = None,
) -> dict:
    """Explode a plan with the batch-multiplier driver.

    Raw materials scale on the exact yield-adjusted output. Stock, supplier and
    cost data are layered on for display and never reduce the requirement.

    Read-only by default. When ``persist=True`` a ``PlanRun`` tagged
    ``driver_version='batchmult-0.2'`` plus its ``PlanRequirement`` tree are
    written in one transaction, isolated from production runs by the tag.
    """
    try:
        plan = Plan.objects.get(pk=plan_id)
    except Plan.DoesNotExist as exc:
        raise PlanningError(f'plan {plan_id} not found') from exc

    inc = increment if increment and increment > 0 else DEFAULT_INCREMENT
    qs = plan.lines.order_by('sort_order', 'id')
    if line_ids is not None:
        qs = qs.filter(id__in=line_ids)
    lines: list[PlanLine] = list(qs)
    if not lines:
        raise PlanningError('plan has no demand lines')

    line_demand = line_demand or {}
    meta = _Meta()
    rollup: dict[int, dict] = {}
    items: list[dict] = []

    def _explode(run: PlanRun | None) -> None:
        for line in lines:
            per_line = line_demand.get(line.id) or line_demand.get(str(line.id))
            merged = {**(demand_inputs or {}), **(per_line or {})}
            inputs = chain_net._normalize_demand_inputs(merged or None)
            manual = inputs['manual_make_qty']
            target = manual if manual is not None else line.quantity
            node = _net_node(
                product_id=line.product_id,
                demand=target,
                depth=0,
                ancestry=frozenset(),
                increment=inc,
                consider_stock=consider_stock,
                plan_date=plan.plan_date,
                meta=meta,
                rollup=rollup,
                run=run,
                plan_line=line,
                parent=None,
            )
            node['plan_line_id'] = line.id
            node['plan_quantity'] = _dec(line.quantity)
            node['demand_breakdown'] = _demand_breakdown(
                target=target,
                stock=Decimal(node.get('stock') or '0'),
                pending=inputs['today_pending_dispatch_qty'],
                wip=inputs['wip_fg_equivalent_qty'],
            )
            items.append(node)

    run_info = None
    if persist:
        now = timezone.now()
        with transaction.atomic():
            run = PlanRun.objects.create(
                plan=plan,
                run_number=_next_run_number(plan),
                status=PlanRunStatus.RUNNING,
                driver_version=DRIVER_VERSION,
                started_at=now,
                stamp_json={
                    'actor_name': actor_name or 'System Admin',
                    'increment': _dec(inc),
                    'consider_stock': consider_stock,
                },
            )
            _explode(run)
            run.status = PlanRunStatus.COMPLETE
            run.completed_at = timezone.now()
            run.save(update_fields=['status', 'completed_at'])
        run_info = {
            'run_id': run.id,
            'run_number': run.run_number,
            'status': run.status,
        }
    else:
        _explode(None)

    return {
        'plan_id': plan.id,
        'plan_date': plan.plan_date.isoformat(),
        'driver_version': DRIVER_VERSION,
        'increment': _dec(inc),
        'consider_stock': consider_stock,
        'persisted': persist,
        'run': run_info,
        'items': items,
        'product_lines': _build_product_lines(items),
        'ingredients': _build_ingredients(rollup),
    }
