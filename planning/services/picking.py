"""Derived picking list from plan requirements (no separate table)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Prefetch

from planning.models import PlanRun
from product.models import ProductSupplier
from stock_ledger.models import (
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util.conversions import StockValidationError, stock_to_kg, stock_to_packs


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
    mappings = list(product.suppliers.all())
    kg = stock_to_kg(net_qty, product)
    fields = {
        'pack_quantity': None,
        'pack_unit_name': None,
        'shape_format_label': None,
        'display_kg': _dec(kg) if kg is not None else None,
    }
    # Box qty is a lie when 10kg and 20kg packs both exist.
    if len(mappings) != 1:
        return fields
    mapping = mappings[0]
    if not mapping.multiplier:
        return fields
    try:
        fields['pack_quantity'] = _dec(stock_to_packs(net_qty, mapping, product))
    except StockValidationError:
        return fields
    fields['pack_unit_name'] = (
        mapping.outer_unit.name if mapping.outer_unit_id else None
    )
    fields['shape_format_label'] = mapping.shape_format_label
    return fields


def issue_qty_by_requirement(
    req_ids: list[int],
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """Issued / queued transfer_out qty keyed by plan_requirement id."""
    issued_by: dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    queued_by: dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    if not req_ids:
        return issued_by, queued_by
    entries = list(
        StockEntry.objects.filter(
            entry_type=StockEntryType.TRANSFER_OUT,
            source_document_type='plan_requirement',
            source_document_id__in=req_ids,
            reversed_by__isnull=True,
        ).values_list('id', 'source_document_id', 'quantity')
    )
    posting_status = dict(
        StockEntryPosting.objects.filter(
            stock_entry_id__in=[row[0] for row in entries],
        ).values_list('stock_entry_id', 'status')
    ) if entries else {}
    for entry_id, src_id, qty in entries:
        status = posting_status.get(entry_id)
        if status == StockEntryPostingStatus.CANCELLED:
            continue
        abs_qty = abs(qty)
        if status == StockEntryPostingStatus.QUEUED:
            queued_by[src_id] += abs_qty
        else:
            issued_by[src_id] += abs_qty
    return issued_by, queued_by


def _attach_issue_progress(lines: list[dict]) -> None:
    """Stamp issued/queued/remaining from plan-linked transfer_out rows."""
    req_ids = [
        rid for line in lines for rid in (line.get('requirement_ids') or [])
    ]
    issued_by, queued_by = issue_qty_by_requirement(req_ids)
    for line in lines:
        ids = line.get('requirement_ids') or []
        issued = sum((issued_by[i] for i in ids), Decimal('0'))
        queued = sum((queued_by[i] for i in ids), Decimal('0'))
        required = Decimal(line['net_quantity'] or '0')
        remaining = required - issued - queued
        if remaining < 0:
            remaining = Decimal('0')
        taken = issued + queued
        if remaining <= 0 and taken > 0:
            status = 'complete'
        elif taken > 0:
            status = 'partial'
        else:
            status = 'open'
        pack = line.get('pack_quantity')
        if pack and required > 0:
            ratio = Decimal(pack) / required
            line['issued_pack_quantity'] = _dec(issued * ratio)
            line['queued_pack_quantity'] = _dec(queued * ratio)
            line['remaining_pack_quantity'] = _dec(remaining * ratio)
        else:
            line['issued_pack_quantity'] = None
            line['queued_pack_quantity'] = None
            line['remaining_pack_quantity'] = None
        display_kg = line.get('display_kg')
        if display_kg and required > 0:
            kg_ratio = Decimal(display_kg) / required
            line['issued_kg'] = _dec(issued * kg_ratio)
            line['queued_kg'] = _dec(queued * kg_ratio)
            line['remaining_kg'] = _dec(remaining * kg_ratio)
        else:
            line['issued_kg'] = None
            line['queued_kg'] = None
            line['remaining_kg'] = None
        line['issued_quantity'] = _dec(issued)
        line['queued_quantity'] = _dec(queued)
        line['remaining_quantity'] = _dec(remaining)
        line['status'] = status


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
                    .select_related('outer_unit', 'inner_unit')
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
    _attach_issue_progress(lines)
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
