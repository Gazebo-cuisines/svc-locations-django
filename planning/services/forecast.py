from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from planning.adapters import product as product_adapter
from planning.adapters import recipe as recipe_adapter
from planning.models import DemandProfile, Plan, PlanStatus
from planning.services import lifecycle, netting
from planning.services.exceptions import PlanningError
from recipe.utils import scaled_child_net

MAX_BOM_DEPTH = 20
DEFAULT_HORIZON_DAYS = 5


def upsert_demand_profile(
    *,
    product_id: int,
    weekday: int,
    mean_quantity: Decimal,
    sample_count: int = 1,
) -> DemandProfile:
    if weekday < 0 or weekday > 6:
        raise PlanningError('weekday must be 0 (Mon) … 6 (Sun)')
    if mean_quantity < 0:
        raise PlanningError('mean_quantity cannot be negative')
    if sample_count < 0:
        raise PlanningError('sample_count cannot be negative')
    now = timezone.now()
    row, _created = DemandProfile.objects.update_or_create(
        product_id=product_id,
        weekday=weekday,
        defaults={
            'mean_quantity': mean_quantity,
            'sample_count': sample_count,
            'computed_at': now,
        },
    )
    return row


def delete_demand_profile(profile_id: int) -> None:
    DemandProfile.objects.filter(pk=profile_id).delete()


def list_demand_profiles(*, product_id: int | None = None) -> list[DemandProfile]:
    qs = DemandProfile.objects.all().order_by('product_id', 'weekday')
    if product_id is not None:
        qs = qs.filter(product_id=product_id)
    return list(qs)


def _bom_gross_needs(
    product_id: int,
    parent_gross: Decimal,
    *,
    depth: int = 0,
    ancestry: set[int] | None = None,
) -> dict[int, Decimal]:
    """In-memory multi-level BOM explosion → component_id → gross qty needed."""
    if parent_gross <= 0 or depth >= MAX_BOM_DEPTH:
        return {}
    ancestry = set(ancestry or ())
    if product_id in ancestry:
        raise PlanningError(f'BOM cycle detected at product {product_id}')
    ancestry = ancestry | {product_id}

    version_id = recipe_adapter.resolve_recipe_version_id(product_id, None)
    if version_id is None:
        return {}
    recipe = recipe_adapter.get_recipe_version(version_id)
    bom_sum = sum((c.quantity for c in recipe.components), Decimal('0'))
    needs: dict[int, Decimal] = {}
    for comp in recipe.components:
        child_gross = scaled_child_net(
            parent_gross,
            comp.quantity,
            batch_quantity=recipe.batch_quantity,
            bom_sum=bom_sum,
            process_batch=recipe.process_batch,
        )
        needs[comp.product_id] = needs.get(comp.product_id, Decimal('0')) + child_gross
        for cid, qty in _bom_gross_needs(
            comp.product_id,
            child_gross,
            depth=depth + 1,
            ancestry=ancestry,
        ).items():
            needs[cid] = needs.get(cid, Decimal('0')) + qty
    return needs


def _available(product_id: int, as_of: date) -> tuple[Decimal, Decimal]:
    try:
        product = product_adapter.get_product_spec(product_id)
    except Exception as exc:
        raise PlanningError(f'product {product_id} not found') from exc
    return netting.available_for_netting(product, plan_date=as_of)


def project_horizon(
    *,
    start_date: date,
    days: int = DEFAULT_HORIZON_DAYS,
    product_id: int | None = None,
) -> list[dict]:
    if days < 1 or days > 31:
        raise PlanningError('days must be between 1 and 31')

    profiles = list_demand_profiles(product_id=product_id)
    by_product_weekday: dict[tuple[int, int], DemandProfile] = {
        (p.product_id, p.weekday): p for p in profiles
    }
    product_ids = sorted({p.product_id for p in profiles})
    if product_id is not None and product_id not in product_ids:
        product_ids = [product_id]

    rows: list[dict] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        weekday = day.weekday()  # Mon=0 … Sun=6
        for pid in product_ids:
            profile = by_product_weekday.get((pid, weekday))
            demand = profile.mean_quantity if profile else Decimal('0')
            if demand <= 0 and profile is None:
                continue
            available, on_hand = _available(pid, day)
            short = max(demand - available, Decimal('0'))
            mean_week = sum(
                (
                    by_product_weekday[(pid, wd)].mean_quantity
                    for wd in range(7)
                    if (pid, wd) in by_product_weekday
                ),
                Decimal('0'),
            )
            active_days = sum(1 for wd in range(7) if (pid, wd) in by_product_weekday)
            avg_daily = (mean_week / active_days) if active_days else Decimal('0')
            runway_days = None
            if avg_daily > 0:
                runway_days = float(on_hand / avg_daily)
            rows.append({
                'date': day,
                'weekday': weekday,
                'product_id': pid,
                'demand': demand,
                'available': available,
                'on_hand': on_hand,
                'shortage': short,
                'runway_days': runway_days,
                'sample_count': profile.sample_count if profile else 0,
            })
    return rows


def shortage_report(
    *,
    start_date: date,
    days: int = DEFAULT_HORIZON_DAYS,
    product_id: int | None = None,
) -> list[dict]:
    """FG + BOM-aware component shortages over the horizon."""
    horizon = project_horizon(
        start_date=start_date,
        days=days,
        product_id=product_id,
    )
    # Aggregate component needs by (date, product)
    component_demand: dict[tuple[date, int], Decimal] = {}
    for row in horizon:
        if row['demand'] <= 0:
            continue
        # Production need ≈ shortage of FG (what we must make), explode that
        to_make = row['shortage'] if row['shortage'] > 0 else Decimal('0')
        # Also explode full demand for visibility of dependent materials if stock covers FG
        # Spec: BOM-aware shortage — explode the demand qty that is not covered by stock
        if to_make <= 0:
            continue
        try:
            needs = _bom_gross_needs(row['product_id'], to_make)
        except PlanningError:
            continue
        for cid, qty in needs.items():
            key = (row['date'], cid)
            component_demand[key] = component_demand.get(key, Decimal('0')) + qty

    shortages: list[dict] = []
    # FG rows that are short
    for row in horizon:
        if row['shortage'] <= 0:
            continue
        shortages.append({
            'date': row['date'],
            'product_id': row['product_id'],
            'level': 'fg',
            'required': row['demand'],
            'available': row['available'],
            'shortage': row['shortage'],
            'on_hand': row['on_hand'],
            'runway_days': row['runway_days'],
        })

    for (day, pid), required in sorted(component_demand.items()):
        available, on_hand = _available(pid, day)
        short = max(required - available, Decimal('0'))
        if short <= 0:
            continue
        shortages.append({
            'date': day,
            'product_id': pid,
            'level': 'component',
            'required': required,
            'available': available,
            'shortage': short,
            'on_hand': on_hand,
            'runway_days': None,
        })
    return shortages


@transaction.atomic
def age_open_plans(
    *,
    as_of: date | None = None,
    actor_user_id: int | None = None,
) -> list[dict]:
    """Close aged draft/locked plans and rollover lines to next calendar day (D2)."""
    as_of = as_of or timezone.localdate()
    aged = list(
        Plan.objects
        .select_for_update()
        .filter(
            plan_date__lt=as_of,
            status__in=[PlanStatus.DRAFT, PlanStatus.LOCKED],
        )
        .order_by('plan_date', 'id')
    )
    results: list[dict] = []
    for plan in aged:
        target_date = plan.plan_date + timedelta(days=1)
        target = lifecycle.rollover_open_lines(
            plan.id,
            target_plan_date=target_date,
            actor_user_id=actor_user_id,
        )
        results.append({
            'source_plan_id': plan.id,
            'target_plan_id': target.id,
            'target_plan_date': target.plan_date,
        })
    return results
