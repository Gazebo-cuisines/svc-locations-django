"""Derived picking list from plan requirements (no separate table)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Prefetch

from planning.models import PlanRun
from product.models import ProductSupplier


def _dec(value) -> str | None:
    return str(value) if value is not None else None


def _category_fields(product) -> dict:
    cat = product.category if product.category_id else None
    if cat is None:
        return {
            'category_id': None,
            'category_name': None,
            'category_path': None,
            'category_l2': None,
        }
    path = cat.path or cat.name
    parts = [p.strip() for p in path.split('>') if p.strip()]
    return {
        'category_id': cat.id,
        'category_name': cat.name,
        'category_path': cat.path,
        'category_l2': parts[1] if len(parts) > 1 else (parts[0] if parts else cat.name),
    }


def _pack_fields(product, net_qty: Decimal) -> dict:
    mapping = next(iter(product.suppliers.all()), None)
    if mapping is None or not mapping.multiplier:
        return {
            'pack_quantity': None,
            'pack_unit_name': None,
            'shape_format_label': None,
        }
    return {
        'pack_quantity': _dec(net_qty / mapping.multiplier),
        'pack_unit_name': (
            mapping.outer_unit.name if mapping.outer_unit_id else None
        ),
        'shape_format_label': mapping.shape_format_label,
    }


def build_picking_list(
    run: PlanRun,
    *,
    from_location: str | None = None,
    to_location: str | None = None,
) -> dict:
    """
    Aggregate open requirements by product + from/to + unit for issue sheets.

    Default excludes closed rows (already covered by stock).
    Optional from_location / to_location (names) scope outbound / inbound.
    Lines include names and ids for portal handoff.
    """
    qs = (
        run.requirements.filter(closed=False)
        .select_related(
            'product',
            'product__unit',
            'product__category',
            'source_location',
            'destination_location',
        )
        .prefetch_related(
            Prefetch(
                'product__suppliers',
                queryset=(
                    ProductSupplier.objects
                    .filter(is_active=True)
                    .select_related('outer_unit')
                    .order_by('-is_default', '-id')
                ),
            ),
        )
        .order_by('id')
    )
    if from_location is not None:
        qs = qs.filter(source_location__name=from_location)
    if to_location is not None:
        qs = qs.filter(destination_location__name=to_location)

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
                'product_id': req.product_id,
                'product': req.product.name,
                '_product': req.product,
                **_category_fields(req.product),
                'gross_quantity': Decimal('0'),
                'net_quantity': Decimal('0'),
                'unit': req.product.unit.name if req.product.unit_id else None,
                'from_location_id': req.source_location_id,
                'from_location': (
                    req.source_location.name if req.source_location_id else None
                ),
                'to_location_id': req.destination_location_id,
                'to_location': (
                    req.destination_location.name
                    if req.destination_location_id
                    else None
                ),
                'requirement_ids': [],
            }
        bucket = buckets[key]
        bucket['gross_quantity'] += req.gross_required or Decimal('0')
        bucket['net_quantity'] += req.net_required or Decimal('0')
        bucket['requirement_ids'].append(req.id)

    lines = []
    for bucket in buckets.values():
        product = bucket.pop('_product')
        net_qty = bucket['net_quantity']
        lines.append({
            **bucket,
            **_pack_fields(product, net_qty),
            'gross_quantity': _dec(bucket['gross_quantity']),
            'net_quantity': _dec(net_qty),
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
        'to_location': to_location,
        'lines': lines,
        'by_department': by_department,
    }
