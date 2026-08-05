"""Plan vs production-register progress (planned requirements × MADE)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.shortcuts import get_object_or_404

from planning.models import Plan, PlanRunStatus
from planning.services.exceptions import PlanningError
from planning.services.portal import resolve_location
from stock_ledger.models import ProductionRun, StockEntryType


def _dec(value: Decimal) -> str:
    return str(value)


def _status(planned: Decimal, done: Decimal) -> str:
    if planned <= 0:
        return 'complete' if done >= 0 else 'open'
    if done <= 0:
        return 'open'
    if done >= planned:
        return 'complete'
    return 'partial'


def _pct(planned: Decimal, done: Decimal) -> str:
    if planned <= 0:
        return '100' if done >= 0 else '0'
    pct = (done / planned) * Decimal('100')
    if pct > Decimal('100'):
        pct = Decimal('100')
    return str(pct.quantize(Decimal('0.01')))


def build_plan_progress(
    plan_id: int,
    *,
    location: str | None = None,
) -> dict:
    """
    Compare open plan requirements (planned) to production MADE on plan_date.

    planned = sum(net_required) by product + from_location
    done    = sum(PRODUCTION_OUTPUT qty) where counterparty = from_location
              and base_date = plan.plan_date
    """
    plan = get_object_or_404(Plan, pk=plan_id)
    run = (
        plan.runs.filter(status=PlanRunStatus.COMPLETE)
        .order_by('-run_number')
        .first()
    )
    if run is None:
        raise PlanningError('Plan has no complete run.')

    loc_filter_id: int | None = None
    loc_filter_name: str | None = None
    if location not in (None, ''):
        loc = resolve_location(location)
        loc_filter_id = loc.id
        loc_filter_name = loc.name

    req_qs = (
        run.requirements.filter(closed=False)
        .exclude(source_location_id__isnull=True)
        .select_related('product', 'product__unit', 'source_location', 'destination_location')
    )
    if loc_filter_id is not None:
        req_qs = req_qs.filter(source_location_id=loc_filter_id)

    planned: dict[tuple[int, int], dict] = {}
    for req in req_qs:
        key = (req.source_location_id, req.product_id)
        if key not in planned:
            planned[key] = {
                'product_id': req.product_id,
                'product': req.product.name,
                'unit': req.product.unit.name if req.product.unit_id else None,
                'from_location_id': req.source_location_id,
                'from_location': req.source_location.name,
                'to_location_id': req.destination_location_id,
                'to_location': (
                    req.destination_location.name
                    if req.destination_location_id
                    else None
                ),
                'planned': Decimal('0'),
                'requirement_ids': [],
            }
        row = planned[key]
        row['planned'] += req.net_required or Decimal('0')
        row['requirement_ids'].append(req.id)

    product_ids = {k[1] for k in planned}
    location_ids = {k[0] for k in planned}
    made_qty: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal('0'))
    last_made: dict[tuple[int, int], str | None] = {}

    if product_ids and location_ids:
        for run_row in (
            ProductionRun.objects.filter(
                base_date=plan.plan_date,
                stock_entry__entry_type=StockEntryType.PRODUCTION_OUTPUT,
                stock_entry__reversed_by__isnull=True,
                stock_entry__counterparty_location_id__in=location_ids,
                stock_entry__lot__product_id__in=product_ids,
            )
            .select_related('stock_entry__lot')
            .order_by('id')
        ):
            key = (
                run_row.stock_entry.counterparty_location_id,
                run_row.stock_entry.lot.product_id,
            )
            made_qty[key] += run_row.stock_entry.quantity or Decimal('0')
            ts = run_row.finished_at or run_row.started_at or run_row.created_at
            if ts is not None:
                last_made[key] = ts.isoformat()

    lines = []
    for key, meta in planned.items():
        done = made_qty.get(key, Decimal('0'))
        planned_qty = meta['planned']
        lines.append({
            'product_id': meta['product_id'],
            'product': meta['product'],
            'unit': meta['unit'],
            'from_location_id': meta['from_location_id'],
            'from_location': meta['from_location'],
            'to_location_id': meta['to_location_id'],
            'to_location': meta['to_location'],
            'planned': _dec(planned_qty),
            'done': _dec(done),
            'pct': _pct(planned_qty, done),
            'status': _status(planned_qty, done),
            'last_made_at': last_made.get(key),
            'requirement_ids': meta['requirement_ids'],
        })

    lines.sort(
        key=lambda row: (
            row['from_location'] or '',
            row['product'] or '',
        ),
    )

    by_dept: dict[int, dict] = {}
    for line in lines:
        loc_id = line['from_location_id']
        if loc_id not in by_dept:
            by_dept[loc_id] = {
                'from_location_id': loc_id,
                'from_location': line['from_location'],
                'line_count': 0,
                'complete_count': 0,
                'partial_count': 0,
                'open_count': 0,
            }
        bucket = by_dept[loc_id]
        bucket['line_count'] += 1
        bucket[f'{line["status"]}_count'] += 1

    by_department = sorted(
        by_dept.values(),
        key=lambda row: row['from_location'] or '',
    )

    return {
        'plan_id': plan.id,
        'run_id': run.id,
        'plan_date': plan.plan_date.isoformat(),
        'location': loc_filter_name,
        'location_id': loc_filter_id,
        'lines': lines,
        'by_department': by_department,
    }
