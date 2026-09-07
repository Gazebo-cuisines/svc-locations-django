"""Collapse split goods-in rows (`{key}:u:1..N`) into one timeline parent."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from purchasing.models import PurchaseOrder
from stock_ledger.models import StockEntry
from stock_ledger.util.entry_labels import entry_code
from stock_ledger.util.reports import _operational_movement_entries

_SPLIT_KEY = re.compile(r'^(?P<base>.+):u:(?P<n>\d+)$')


def receive_group_key(idempotency_key: str | None) -> str | None:
    if not idempotency_key:
        return None
    match = _SPLIT_KEY.match(idempotency_key)
    return match.group('base') if match else None


def po_numbers_for_entries(entries: list[StockEntry]) -> dict[int, str]:
    """Sage/system PO ref for receipts that omitted stock_entry.po_number."""
    ids = {
        entry.source_document_id
        for entry in entries
        if not (entry.po_number or '').strip()
        and entry.source_document_type == 'po'
        and entry.source_document_id
    }
    if not ids:
        return {}
    return {
        po.id: (po.external_number or po.number)
        for po in PurchaseOrder.objects.filter(pk__in=ids).only(
            'id', 'number', 'external_number',
        )
        if (po.external_number or po.number)
    }


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _unit_row(row: dict) -> dict:
    return {
        'entry_id': row['entry_id'],
        'entry_code': row['entry_code'],
        'unit_serial': row['entry_code'],
        'quantity': row['quantity'],
        'posting_status': row.get('posting_status'),
        'is_live': row.get('is_live'),
    }


def posted_history_qs(
    *,
    product_id: int,
    entry_types: tuple[str, ...],
    location_id: int | None = None,
):
    """Posted, not-reversed movements for product GI/GO history."""
    qs = (
        _operational_movement_entries()
        .select_related(
            'unit',
            'location',
            'counterparty_location',
            'lot__product',
            'lot__product_supplier__outer_unit',
            'posting',
            'label',
        )
        .filter(lot__product_id=product_id, entry_type__in=entry_types)
        .order_by('-recorded_at', '-id')
    )
    if location_id is not None:
        qs = qs.filter(location_id=location_id)
    return qs


def expand_split_siblings(
    qs,
    page_entries: list[StockEntry],
) -> tuple[list[StockEntry], set[int]]:
    """Pull the rest of any `:u:` group that appears on this page."""
    page_ids = {entry.id for entry in page_entries}
    prefixes = {
        receive_group_key(entry.idempotency_key) for entry in page_entries
    }
    prefixes.discard(None)
    if not prefixes:
        return page_entries, page_ids
    extra_q = Q()
    for prefix in prefixes:
        extra_q |= Q(idempotency_key__startswith=f'{prefix}:u:')
    by_id = {entry.id: entry for entry in page_entries}
    for entry in qs.filter(extra_q):
        if receive_group_key(entry.idempotency_key) in prefixes:
            by_id[entry.id] = entry
    return list(by_id.values()), page_ids


def consolidate_audit_items(
    items: list[dict],
    *,
    page_ids: set[int],
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    leftovers: list[dict] = []
    for row in items:
        key = row.get('receive_group_key')
        if key:
            grouped[key].append(row)
        elif row['entry_id'] in page_ids:
            leftovers.append(row)

    out = list(leftovers)
    for key, rows in grouped.items():
        newest = max(rows, key=lambda r: (r.get('at') or '', r['entry_id']))
        if newest['entry_id'] not in page_ids:
            continue
        parent_id = min(r['entry_id'] for r in rows)
        parent = dict(newest)
        parent['entry_id'] = parent_id
        parent['entry_code'] = entry_code(parent_id)
        parent['parent_entry_id'] = parent_id
        parent['receive_group_key'] = key
        parent['unit_count'] = len(rows)
        parent['quantity'] = _dec(
            sum(Decimal(str(r.get('quantity') or 0)) for r in rows),
        )
        bases = [
            Decimal(str(r['quantity_base']))
            for r in rows
            if r.get('quantity_base') not in (None, '')
        ]
        if bases:
            parent['quantity_base'] = _dec(sum(bases))
        packs = [
            Decimal(str(r['pack_quantity']))
            for r in rows
            if r.get('pack_quantity') not in (None, '')
        ]
        parent['pack_quantity'] = _dec(sum(packs) if packs else len(rows))
        parent['units'] = [
            _unit_row(r) for r in sorted(rows, key=lambda r: r['entry_id'])
        ]
        out.append(parent)

    out.sort(key=lambda r: (r.get('at') or '', r['entry_id']), reverse=True)
    return out
