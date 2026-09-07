"""Goods-out wizard flags. Not goods-in: no QC.

Without plan (green rail): Find → Qty → FIFO → Scan
Then Queue (POST transfer) → Print → Verify (scan OUT to post).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.utils import timezone

from locations.models import Location
from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util import entry_labels, entry_posting


class GoodsOutFormError(ValueError):
    pass


RAIL = ('find', 'qty', 'fifo', 'scan')
ADHOC_ORDER = (*RAIL, 'queue', 'print', 'verify')
PLAN_ORDER = ('queue', 'print', 'verify')


def _labels_done(labels: list[dict], flag: str) -> bool:
    return bool(labels) and all(row[flag] for row in labels)


def label_row(entry: StockEntry) -> dict:
    flags = entry_posting.queued_step_flags(entry)
    if flags['posted']:
        flags['print_label'] = True
        flags['verify_label'] = True
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        **flags,
    }


def _print_verify(labels: list[dict]) -> tuple[bool, bool]:
    return (
        _labels_done(labels, 'print_label'),
        _labels_done(labels, 'posted'),
    )


def adhoc_line_steps(*, line_id: int | None, labels: list[dict]) -> dict:
    queued = bool(labels)
    printed, verified = _print_verify(labels)
    return {
        'line_id': line_id,
        'find': queued,
        'qty': queued,
        'fifo': queued,
        'scan': queued,
        'queue': queued,
        'print': printed,
        'verify': verified,
        'labels': labels,
    }


def plan_line_steps(*, line_id: int | None, labels: list[dict]) -> dict:
    printed, verified = _print_verify(labels)
    return {
        'line_id': line_id,
        'queue': bool(labels),
        'print': printed,
        'verify': verified,
        'labels': labels,
    }


def current_step(rows: list[dict], order: tuple[str, ...]) -> str:
    done = order[-1]
    active = [row for row in rows if row.get('queue') and not row.get(done)]
    for row in active or rows:
        for flag in order:
            if not row[flag]:
                return flag
    return 'done'


def form_steps(
    rows: list[dict],
    answers_lines: dict,
    *,
    order: tuple[str, ...],
    rail: tuple[str, ...] | None = None,
) -> dict:
    payload = {
        'steps': {
            'current': current_step(rows, order),
            'lines': rows,
        },
        'answers': {'lines': answers_lines},
    }
    if rail is not None:
        payload['steps']['rail'] = list(rail)
    return payload


def _qty_str(value: Decimal) -> str:
    return str(value) if value != 0 else '0'


def _posted_on(entry: StockEntry, day: date) -> bool:
    posting = entry_posting.get_posting(entry)
    if posting is None or posting.status != StockEntryPostingStatus.POSTED:
        return False
    if posting.posted_at is None:
        return True
    return timezone.localtime(posting.posted_at).date() == day


def _is_queued(entry: StockEntry) -> bool:
    posting = entry_posting.get_posting(entry)
    return posting is not None and posting.status == StockEntryPostingStatus.QUEUED


def resolve_adhoc_goods_out_form(location_id: int) -> dict:
    loc = Location.objects.filter(pk=location_id, visible=True).first()
    if loc is None:
        raise GoodsOutFormError('Location not found.')

    today = timezone.localdate()
    entries = (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.TRANSFER_OUT,
            source_document_type='goods_out_adhoc',
            location_id=location_id,
            reversed_by__isnull=True,
        )
        .exclude(posting__status=StockEntryPostingStatus.CANCELLED)
        .select_related('label', 'posting', 'lot')
        .order_by('id')
    )
    live = [e for e in entries if _is_queued(e) or _posted_on(e, today)]

    by_product: dict[int, list[StockEntry]] = defaultdict(list)
    for entry in live:
        product_id = entry.lot.product_id if entry.lot_id else None
        if product_id is None:
            continue
        by_product[product_id].append(entry)

    step_rows = []
    answers_lines: dict[str, dict] = {}
    for product_id, group in by_product.items():
        labels = [label_row(entry) for entry in group]
        step_rows.append(adhoc_line_steps(line_id=product_id, labels=labels))
        queued = Decimal('0')
        issued = Decimal('0')
        for entry, label in zip(group, labels):
            qty = abs(entry.quantity)
            if label['posted']:
                issued += qty
            else:
                queued += qty
        answers_lines[str(product_id)] = {
            'qty_queued': _qty_str(queued),
            'qty_issued': _qty_str(issued),
        }

    payload = form_steps(
        step_rows, answers_lines, order=ADHOC_ORDER, rail=RAIL,
    )
    if not step_rows:
        payload['steps']['current'] = 'find'
    payload['location_id'] = loc.id
    payload['location_name'] = loc.name
    return payload
