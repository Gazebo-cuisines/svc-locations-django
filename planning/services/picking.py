"""Derived picking list from plan requirements (no separate table)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from planning.models import PlanRun


def _dec(value) -> str | None:
    return str(value) if value is not None else None


def build_picking_list(
    run: PlanRun,
    *,
    from_location: str | None = None,
) -> dict:
    """
    Aggregate open requirements by product + from/to + unit for issue sheets.

    Default excludes closed rows (already covered by stock).
    Optional from_location (name) limits to one issuing department.
    Response exposes names only (no ids).
    """
    qs = (
        run.requirements.filter(closed=False)
        .select_related(
            'product',
            'product__unit',
            'source_location',
            'destination_location',
        )
        .order_by('id')
    )
    if from_location is not None:
        qs = qs.filter(source_location__name=from_location)

    buckets: dict[tuple, dict] = {}
    for req in qs:
        unit_id = req.product.unit_id
        key = (
            req.product_id,
            req.source_location_id,
            req.destination_location_id,
            unit_id,
        )
        if key not in buckets:
            buckets[key] = {
                'product': req.product.name,
                'gross_quantity': Decimal('0'),
                'net_quantity': Decimal('0'),
                'unit': req.product.unit.name if req.product.unit_id else None,
                'from_location': (
                    req.source_location.name if req.source_location_id else None
                ),
                'to_location': (
                    req.destination_location.name
                    if req.destination_location_id
                    else None
                ),
            }
        bucket = buckets[key]
        bucket['gross_quantity'] += req.gross_required or Decimal('0')
        bucket['net_quantity'] += req.net_required or Decimal('0')

    lines = []
    for bucket in buckets.values():
        lines.append({
            **bucket,
            'gross_quantity': _dec(bucket['gross_quantity']),
            'net_quantity': _dec(bucket['net_quantity']),
        })
    lines.sort(
        key=lambda row: (
            row['from_location'] or '',
            row['to_location'] or '',
            row['product'] or '',
        ),
    )

    dept_qs = (
        run.requirements.filter(closed=False)
        .exclude(source_location_id__isnull=True)
        .select_related('source_location')
    )
    dept_lines: dict[int, set[tuple]] = defaultdict(set)
    dept_names: dict[int, str] = {}
    for req in dept_qs:
        loc_id = req.source_location_id
        dept_names[loc_id] = req.source_location.name
        dept_lines[loc_id].add((
            req.product_id,
            req.source_location_id,
            req.destination_location_id,
            req.product.unit_id,
        ))

    by_department = [
        {
            'from_location': dept_names[loc_id],
            'line_count': len(keys),
        }
        for loc_id, keys in sorted(
            dept_lines.items(),
            key=lambda item: dept_names[item[0]],
        )
    ]

    return {
        'from_location': from_location,
        'lines': lines,
        'by_department': by_department,
    }
