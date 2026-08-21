"""BOM floor-allocate completeness for a production MADE row."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from locations.models import LocationStockProfile
from recipe.models import RecipeComponent
from recipe.utils import scaled_child_net
from stock_ledger.models import StockEntry, StockEntryType
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.services import (
    PRODUCTION_SOURCE_DOC,
    _component_yield_factor,
    _require_production_output,
)

STATUS_COMPLETE = 'complete'
STATUS_INCOMPLETE = 'incomplete'
STATUS_NO_RECIPE = 'no_recipe'


def _empty_complete(*, output_entry_id: int, status: str = STATUS_NO_RECIPE) -> dict:
    return {
        'output_entry_id': output_entry_id,
        'allocation_status': status,
        'remaining_component_count': 0,
        'incomplete_reasons': [],
        'remaining_lines': [],
    }


def _lines_from_components(
    *,
    made: Decimal,
    process_loss: Decimal,
    components,
    consumed_map: dict[int, Decimal],
    batch_quantity: Decimal | None = None,
) -> list[dict]:
    if process_loss <= 0:
        process_loss = Decimal('1')
    parent_gross = abs(made) / process_loss
    bom_sum = sum((c.quantity for c in components), Decimal('0'))
    lines = []
    for comp in components:
        yf = _component_yield_factor(comp.component_product)
        needed = scaled_child_net(
            parent_gross,
            comp.quantity,
            yield_factor=yf,
            batch_quantity=batch_quantity,
            bom_sum=bom_sum,
        ).quantize(Decimal('0.000001'))
        consumed = consumed_map.get(comp.component_product_id, Decimal('0'))
        remaining = max(needed - consumed, Decimal('0')).quantize(Decimal('0.000001'))
        lines.append({
            'component_product_id': comp.component_product_id,
            'component_product_name': comp.component_product.name,
            'needed_quantity': str(needed),
            'consumed_quantity': str(consumed),
            'remaining_quantity': str(remaining),
            'unit_id': comp.unit_id,
            'unit_name': comp.unit.name if comp.unit_id else None,
        })
    return lines


def _status_from_lines(output_entry_id: int, lines: list[dict]) -> dict:
    remaining_lines = [
        line for line in lines
        if Decimal(line['remaining_quantity']) > 0
    ]
    if not lines:
        return _empty_complete(output_entry_id=output_entry_id, status=STATUS_COMPLETE)

    if not remaining_lines:
        return {
            'output_entry_id': output_entry_id,
            'allocation_status': STATUS_COMPLETE,
            'remaining_component_count': 0,
            'incomplete_reasons': [],
            'remaining_lines': [],
        }

    reasons = []
    for line in remaining_lines:
        unit = line['unit_name'] or 'units'
        reasons.append(
            f"{line['component_product_name']} still needs "
            f"{line['remaining_quantity']} {unit}"
        )
    return {
        'output_entry_id': output_entry_id,
        'allocation_status': STATUS_INCOMPLETE,
        'remaining_component_count': len(remaining_lines),
        'incomplete_reasons': reasons,
        'remaining_lines': remaining_lines,
    }


def allocation_status(*, output_entry_id: int) -> dict:
    """
    complete | incomplete | no_recipe.

    no_recipe / empty BOM → complete for Dispatch visibility (nothing to allocate).
    """
    try:
        output = _require_production_output(output_entry_id)
    except StockValidationError:
        raise

    lot = output.lot
    if not lot.recipe_version_id:
        return _empty_complete(output_entry_id=output.id, status=STATUS_NO_RECIPE)

    version = lot.recipe_version
    process_loss = version.process_loss or Decimal('1')
    components = list(
        RecipeComponent.objects
        .filter(recipe_version_id=version.id)
        .select_related('component_product__yield_data', 'unit')
        .order_by('line_no')
    )
    if not components:
        return _empty_complete(output_entry_id=output.id, status=STATUS_NO_RECIPE)

    consumed_map = _consumed_map_for_outputs([output.id]).get(output.id, {})
    lines = _lines_from_components(
        made=output.quantity,
        process_loss=process_loss,
        components=components,
        consumed_map=consumed_map,
        batch_quantity=version.batch_quantity,
    )
    return _status_from_lines(output.id, lines)


def _consumed_map_for_outputs(output_ids: list[int]) -> dict[int, dict[int, Decimal]]:
    """output_entry_id → {component_product_id → consumed qty}."""
    out: dict[int, dict[int, Decimal]] = {oid: {} for oid in output_ids}
    if not output_ids:
        return out
    rows = (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id__in=output_ids,
        )
        .values('source_document_id', 'lot__product_id')
        .annotate(total=Sum('quantity'))
    )
    for row in rows:
        oid = row['source_document_id']
        pid = row['lot__product_id']
        if oid is None or pid is None:
            continue
        out.setdefault(oid, {})[pid] = abs(row['total'] or Decimal('0'))
    return out


def allocation_status_for_entries(output_entry_ids: list[int]) -> dict[int, dict]:
    """Batch status for a production list page. Keys are entry ids."""
    if not output_entry_ids:
        return {}

    entries = list(
        StockEntry.objects
        .filter(
            pk__in=output_entry_ids,
            entry_type=StockEntryType.PRODUCTION_OUTPUT,
        )
        .select_related('lot__recipe_version', 'lot__product')
    )
    by_id = {e.id: e for e in entries}
    result: dict[int, dict] = {}

    version_ids = {
        e.lot.recipe_version_id
        for e in entries
        if e.lot.recipe_version_id
    }
    components_by_version: dict[int, list] = {vid: [] for vid in version_ids}
    if version_ids:
        for comp in (
            RecipeComponent.objects
            .filter(recipe_version_id__in=version_ids)
            .select_related('component_product__yield_data', 'unit')
            .order_by('recipe_version_id', 'line_no')
        ):
            components_by_version.setdefault(comp.recipe_version_id, []).append(comp)

    consumed_by_output = _consumed_map_for_outputs(list(by_id.keys()))

    for oid in output_entry_ids:
        entry = by_id.get(oid)
        if entry is None:
            result[oid] = _empty_complete(output_entry_id=oid, status=STATUS_NO_RECIPE)
            continue
        lot = entry.lot
        if not lot.recipe_version_id:
            result[oid] = _empty_complete(output_entry_id=oid, status=STATUS_NO_RECIPE)
            continue
        components = components_by_version.get(lot.recipe_version_id, [])
        if not components:
            result[oid] = _empty_complete(output_entry_id=oid, status=STATUS_NO_RECIPE)
            continue
        process_loss = lot.recipe_version.process_loss or Decimal('1')
        lines = _lines_from_components(
            made=entry.quantity,
            process_loss=process_loss,
            components=components,
            consumed_map=consumed_by_output.get(oid, {}),
            batch_quantity=lot.recipe_version.batch_quantity,
        )
        result[oid] = _status_from_lines(oid, lines)

    return result


def is_dispatch_visible(status: str) -> bool:
    """Incomplete BOM holds stock from Dispatch; no_recipe and complete are visible."""
    return status != STATUS_INCOMPLETE


def incomplete_production_lot_ids(lot_ids: set[int] | list[int]) -> set[int]:
    """
    Lots whose latest unreversed PRODUCTION_OUTPUT still has BOM remaining.
    Purchase / opening lots (no MADE output) are never returned.
    """
    ids = {int(x) for x in lot_ids if x is not None}
    if not ids:
        return set()

    outputs = list(
        StockEntry.objects
        .filter(
            lot_id__in=ids,
            entry_type=StockEntryType.PRODUCTION_OUTPUT,
            reversed_by__isnull=True,
        )
        .order_by('lot_id', '-id')
        .values_list('id', 'lot_id')
    )
    # One MADE row per lot — use the newest output if several exist.
    entry_by_lot: dict[int, int] = {}
    for entry_id, lot_id in outputs:
        if lot_id not in entry_by_lot:
            entry_by_lot[lot_id] = entry_id

    if not entry_by_lot:
        return set()

    statuses = allocation_status_for_entries(list(entry_by_lot.values()))
    held: set[int] = set()
    for lot_id, entry_id in entry_by_lot.items():
        status = statuses.get(entry_id, {})
        if status.get('allocation_status') == STATUS_INCOMPLETE:
            held.add(lot_id)
    return held


def location_hides_incomplete_stock(location_id: int) -> bool:
    """True when balances at this location should suppress incomplete MADE lots."""
    profile = (
        LocationStockProfile.objects
        .filter(pk=location_id)
        .only('show_incomplete_stock')
        .first()
    )
    if profile is None:
        return True  # no profile → safe default = hide incomplete
    return not profile.show_incomplete_stock


def exclude_incomplete_lot_ids(
    *,
    location_id: int | None,
    lot_ids: set[int] | list[int],
    include_incomplete: bool = False,
) -> set[int]:
    """
    Lot ids to drop from a location-scoped stock read.
    Empty when location unset, override on, or department allows incomplete.
    """
    if include_incomplete or location_id is None:
        return set()
    if not location_hides_incomplete_stock(int(location_id)):
        return set()
    return incomplete_production_lot_ids(lot_ids)


def held_balance_keys(
    balances,
    *,
    include_incomplete: bool = False,
) -> set[tuple[int, int]]:
    """
    (location_id, lot_id) pairs to drop from a balances read.
    Honours each location's show_incomplete_stock (works for global lists).
    """
    if include_incomplete:
        return set()
    by_loc: dict[int, set[int]] = {}
    for b in balances:
        by_loc.setdefault(int(b.location_id), set()).add(int(b.lot_id))
    held: set[tuple[int, int]] = set()
    for loc_id, lot_ids in by_loc.items():
        for lot_id in exclude_incomplete_lot_ids(
            location_id=loc_id,
            lot_ids=lot_ids,
            include_incomplete=False,
        ):
            held.add((loc_id, lot_id))
    return held
