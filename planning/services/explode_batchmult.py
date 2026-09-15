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

from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from product.models import Product, Unit

from planning.adapters import product as product_adapter
from planning.adapters import recipe as recipe_adapter
from planning.models import Plan, PlanLine, ResourceProductRate
from planning.services import netting
from planning.services.exceptions import PlanningError
from recipe.utils import batch_scale_denom, scaled_child_net
from stock_ledger.util.conversions import StockValidationError, to_product_unit

DRIVER_VERSION = 'batchmult-0.1'
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


def _to_kg(qty: Decimal, unit_name: str | None) -> Decimal | None:
    unit = (unit_name or '').strip().lower()
    if unit in {'g', 'gram', 'grams', 'gm', 'gms'}:
        return qty / Decimal('1000')
    if unit in {'kg', 'kgs', 'kilogram', 'kilograms'}:
        return qty
    if unit in {'mg', 'milligram', 'milligrams'}:
        return qty / Decimal('1000000')
    return None


class _Meta:
    """Lightweight per-product lookup cache (name / code / category / unit)."""

    def __init__(self) -> None:
        self._products: dict[int, Product] = {}
        self._one_mix: dict[int, Decimal | None] = {}
        self._unit_names: dict[int, str | None] = {}

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
) -> Decimal:
    if (
        bom_unit_id is None
        or stock_unit_id is None
        or bom_unit_id == stock_unit_id
    ):
        return qty
    try:
        product = Product.objects.get(pk=product_id)
        return to_product_unit(qty, bom_unit_id, product)
    except StockValidationError:
        return qty


def _accumulate(rollup: dict[int, dict], *, product: Product, qty: Decimal) -> None:
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
        }
    else:
        acc['quantity'] += qty


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
        _accumulate(rollup, product=product, qty=demand)
        return {
            'product_id': product_id,
            'product_name': product.name,
            'recipe_code': product.recipe_code,
            'unit_id': product.unit_id,
            'has_recipe': False,
            'demand': _dec(demand),
            'children': [],
        }

    spec = product_adapter.get_product_spec(product_id, process_loss=recipe_spec.process_loss)
    process_loss = recipe_spec.process_loss or Decimal('1')
    if process_loss <= 0:
        process_loss = Decimal('1')

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
        child_net = _convert_to_stock(
            child_net,
            bom_unit_id=component.unit_id,
            stock_unit_id=child_product.unit_id,
            product_id=child_product.id,
        )
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
            )
        )

    denom = batch_scale_denom(
        Decimal('0'),
        batch_quantity=recipe_spec.batch_quantity,
        bom_sum=bom_sum,
        process_batch=recipe_spec.process_batch,
        parent_recipe_code=recipe_spec.recipe_code,
    )
    return {
        'product_id': product_id,
        'product_name': product.name,
        'recipe_code': product.recipe_code,
        'unit_id': product.unit_id,
        'has_recipe': True,
        'recipe_version_id': version_id,
        'regime': regime,
        'demand': _dec(demand),
        'net_demand': _dec(net_demand),
        'stock_applied': _dec(stock_applied),
        'process_loss': _dec(process_loss),
        # Raw materials use this exact yield-adjusted output.
        'effective_output': _dec(effective),
        # Production schedule only (rounded); does NOT scale raw materials.
        'batch_yield': _dec(batch_yield),
        'production_batches': _dec(batches),
        'production_output': _dec(production_output),
        'scale_denominator': _dec(denom),
        'explanation': _explain(
            regime=regime,
            demand=net_demand,
            process_loss=process_loss,
            batch_yield=batch_yield,
            batches=batches,
            effective=effective,
            production_output=production_output,
            increment=increment,
        ),
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
    out = []
    for pid, acc in sorted(
        rollup.items(),
        key=lambda kv: (kv[1]['product_name'] or '', kv[0]),
    ):
        qty = acc['quantity']
        kg = _to_kg(qty, acc['unit_name'])
        out.append({
            'product_id': pid,
            'product_name': acc['product_name'],
            'recipe_code': acc['recipe_code'],
            'unit_id': acc['unit_id'],
            'unit_name': acc['unit_name'],
            'quantity': _dec(qty),
            'kg': _dec(kg) if kg is not None else None,
        })
    return out


def run_batchmult_plan(
    plan_id: int,
    *,
    line_ids: list[int] | None = None,
    increment: Decimal | None = None,
    consider_stock: bool = False,
) -> dict:
    """Explode a plan with the batch-multiplier driver. Read-only."""
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

    meta = _Meta()
    rollup: dict[int, dict] = {}
    items: list[dict] = []
    for line in lines:
        node = _net_node(
            product_id=line.product_id,
            demand=line.quantity,
            depth=0,
            ancestry=frozenset(),
            increment=inc,
            consider_stock=consider_stock,
            plan_date=plan.plan_date,
            meta=meta,
            rollup=rollup,
        )
        node['plan_line_id'] = line.id
        items.append(node)

    return {
        'plan_id': plan.id,
        'plan_date': plan.plan_date.isoformat(),
        'driver_version': DRIVER_VERSION,
        'increment': _dec(inc),
        'consider_stock': consider_stock,
        'items': items,
        'ingredients': _build_ingredients(rollup),
    }
