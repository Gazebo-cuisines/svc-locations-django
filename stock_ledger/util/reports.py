"""Stock movement + closing-stock report queries."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from product.models import Product, ProductSupplier
from stock_ledger.models import (
    StockEntry,
    StockEntryLabelScan,
    StockEntryLabelScanResult,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
    StockLot,
)
from stock_ledger.util.product_supplier_lookup import (
    product_supplier_for_entry,
    product_supplier_for_lot,
)
from stock_ledger.util.serialize import supplier_pack_fields
from users_rbac.models import RbacUser

_POSTED_EXCLUDE = [
    StockEntryPostingStatus.QUEUED,
    StockEntryPostingStatus.CANCELLED,
]

_MOVEMENT_SELECT = (
    'unit',
    'location',
    'counterparty_location',
    'lot__product',
    'lot__product__unit',
    'lot__product_supplier__outer_unit',
    'lot__product_supplier__inner_unit',
    'lot__shape_format',
)

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

# Warehouse goods-out posts transfer_out; plain issue stays for legacy/write-offs.
_GOODS_OUT_TYPES = (
    StockEntryType.ISSUE,
    StockEntryType.TRANSFER_OUT,
)


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _as_of_end(as_of: date):
    """Inclusive end of calendar day in the active timezone."""
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(as_of, time(23, 59, 59)), tz)


def _posted_entries():
    return StockEntry.objects.exclude(posting__status__in=_POSTED_EXCLUDE)


def _operational_movement_entries():
    """Posted rows still visible on goods-in/out (excludes manager-removed originals)."""
    return _posted_entries().filter(reversed_by__isnull=True)


def _apply_product_filters(qs, *, product_id=None, goods_in_type=None, location_id=None):
    if product_id is not None:
        qs = qs.filter(lot__product_id=product_id)
    if goods_in_type not in (None, ''):
        qs = qs.filter(lot__product__goods_in_type=goods_in_type)
    if location_id is not None:
        qs = qs.filter(location_id=location_id)
    return qs


def movement_row(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product if lot is not None else None
    location = entry.location
    # quantity is always product.unit (Kg / Litre / …) after ledger write.
    unit = product.unit if product is not None and product.unit_id else entry.unit
    counterparty = entry.counterparty_location
    mapping = product_supplier_for_entry(entry)
    stock_qty = abs(entry.quantity) if entry.quantity is not None else Decimal('0')
    pack = supplier_pack_fields(stock_qty, product, mapping)
    return {
        'entry_id': entry.id,
        'entry_type': entry.entry_type,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'product_id': product.id if product is not None else None,
        'product_name': product.name if product is not None else None,
        'recipe_code': product.recipe_code if product is not None else None,
        'gff_code': product.gff_code if product is not None else None,
        'goods_in_type': product.goods_in_type if product is not None else None,
        'lot_id': entry.lot_id,
        'trace_number': lot.trace_number if lot is not None else None,
        'production_date': (
            lot.production_date.isoformat()
            if lot is not None and lot.production_date
            else None
        ),
        'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
        'location_id': entry.location_id,
        'location_name': location.name if location is not None else None,
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            counterparty.name if counterparty is not None else None
        ),
        'quantity': _dec(entry.quantity),
        'quantity_base': _dec(entry.quantity_base),
        'unit_id': unit.id if unit is not None else entry.unit_id,
        'unit_name': unit.name if unit is not None else None,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'po_number': entry.po_number,
        'remarks': entry.remarks,
        **pack,
    }


def movements_report(
    *,
    date_from: date,
    date_to: date,
    entry_type: str | None = None,
    entry_types: list[str] | tuple[str, ...] | None = None,
    product_id: int | None = None,
    goods_in_type: str | None = None,
    location_id: int | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """Posted movement rows by effective_at date range (inclusive)."""
    types = list(entry_types) if entry_types else (
        [entry_type] if entry_type else []
    )
    if not types:
        raise ValueError('entry_type or entry_types is required')
    row_limit = max(1, min(int(limit), _MAX_LIMIT))
    qs = (
        _operational_movement_entries()
        .filter(
            entry_type__in=types,
            effective_at__date__gte=date_from,
            effective_at__date__lte=date_to,
        )
        .select_related(*_MOVEMENT_SELECT)
        .order_by('effective_at', 'id')
    )
    qs = _apply_product_filters(
        qs,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
    )
    return [movement_row(entry) for entry in qs[:row_limit]]


def goods_out_movements_report(**kwargs) -> list[dict]:
    """Issues + warehouse transfer_out (actual goods-out path)."""
    return movements_report(entry_types=_GOODS_OUT_TYPES, **kwargs)


def closing_balances_as_of(
    *,
    as_of: date,
    product_id: int | None = None,
    goods_in_type: str | None = None,
    location_id: int | None = None,
    include_zero: bool = False,
) -> list[dict]:
    """
    Lot×location closing qty: SUM(quantity) where effective_at <= end of as_of day.
    Excludes queued/cancelled postings and downtime (qty 0, non-stock).
    quantity / unit_name = product stock unit (Kg or Litre). display_kg = kg mass.
    """
    cutoff = _as_of_end(as_of)
    qs = (
        _posted_entries()
        .filter(effective_at__lte=cutoff)
        .exclude(entry_type=StockEntryType.DOWNTIME)
        .filter(lot__product__is_active=True)
    )
    qs = _apply_product_filters(
        qs,
        product_id=product_id,
        goods_in_type=goods_in_type,
        location_id=location_id,
    )

    aggregated = list(
        qs.values(
            'lot_id',
            'location_id',
            'lot__product_id',
            'lot__product__name',
            'lot__product__recipe_code',
            'lot__product__gff_code',
            'lot__product__goods_in_type',
            'lot__product__unit_id',
            'lot__product__unit__name',
            'lot__trace_number',
            'lot__production_date',
            'lot__use_by',
            'location__name',
        )
        .annotate(
            quantity=Sum('quantity'),
            quantity_base=Sum('quantity_base'),
        )
        .order_by('lot__product__name', 'lot_id', 'location_id')
    )

    lot_ids = {row['lot_id'] for row in aggregated if row['lot_id'] is not None}
    lots_by_id = {
        lot.id: lot
        for lot in StockLot.objects.filter(id__in=lot_ids).select_related(
            'product',
            'product__unit',
            'product_supplier__outer_unit',
            'product_supplier__inner_unit',
            'shape_format',
        )
    }

    rows: list[dict] = []
    for row in aggregated:
        qty = row['quantity'] or Decimal('0')
        if not include_zero and qty == 0:
            continue
        use_by = row['lot__use_by']
        production_date = row['lot__production_date']
        lot = lots_by_id.get(row['lot_id'])
        product = lot.product if lot is not None else Product(
            id=row['lot__product_id'],
            unit_id=row['lot__product__unit_id'],
        )
        mapping = product_supplier_for_lot(lot)
        pack = supplier_pack_fields(abs(qty), product, mapping)
        rows.append({
            'as_of': as_of.isoformat(),
            'product_id': row['lot__product_id'],
            'product_name': row['lot__product__name'],
            'recipe_code': row['lot__product__recipe_code'],
            'gff_code': row['lot__product__gff_code'],
            'goods_in_type': row['lot__product__goods_in_type'],
            'lot_id': row['lot_id'],
            'trace_number': row['lot__trace_number'],
            'production_date': (
                production_date.isoformat() if production_date else None
            ),
            'use_by': use_by.isoformat() if use_by else None,
            'location_id': row['location_id'],
            'location_name': row['location__name'],
            'unit_id': row['lot__product__unit_id'],
            'unit_name': row['lot__product__unit__name'],
            'quantity': _dec(qty),
            'quantity_base': _dec(row['quantity_base']),
            **pack,
        })
    return rows


def consolidate_closing_balances(detail_rows: list[dict]) -> list[dict]:
    """
    Roll up lot×location detail into product × product_supplier (shape) totals.
    earliest/latest use_by and production_date across lots in the group.
    """
    buckets: dict[tuple, dict] = {}
    for row in detail_rows:
        key = (row.get('product_id'), row.get('product_supplier_id'))
        qty = Decimal(str(row.get('quantity') or 0))
        qty_base = row.get('quantity_base')
        base = Decimal(str(qty_base)) if qty_base not in (None, '') else None
        use_by = row.get('use_by')
        production_date = row.get('production_date')
        loc_id = row.get('location_id')
        lot_id = row.get('lot_id')

        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                'as_of': row.get('as_of'),
                'product_id': row.get('product_id'),
                'product_name': row.get('product_name'),
                'recipe_code': row.get('recipe_code'),
                'gff_code': row.get('gff_code'),
                'goods_in_type': row.get('goods_in_type'),
                'unit_id': row.get('unit_id'),
                'unit_name': row.get('unit_name'),
                'product_supplier_id': row.get('product_supplier_id'),
                'supplier_code': row.get('supplier_code'),
                'sage_product_code': row.get('sage_product_code'),
                'supplier_product_name': row.get('supplier_product_name'),
                'shape_format_label': row.get('shape_format_label'),
                'pack_unit_name': row.get('pack_unit_name'),
                '_qty': qty,
                '_qty_base': base,
                '_lot_ids': {lot_id} if lot_id is not None else set(),
                '_loc_ids': {loc_id} if loc_id is not None else set(),
                '_use_bys': {use_by} if use_by else set(),
                '_production_dates': (
                    {production_date} if production_date else set()
                ),
                '_product': Product(
                    id=row.get('product_id'),
                    unit_id=row.get('unit_id'),
                ) if row.get('product_id') is not None else None,
            }
            continue

        bucket['_qty'] += qty
        if base is not None:
            bucket['_qty_base'] = (
                (bucket['_qty_base'] or Decimal('0')) + base
            )
        if lot_id is not None:
            bucket['_lot_ids'].add(lot_id)
        if loc_id is not None:
            bucket['_loc_ids'].add(loc_id)
        if use_by:
            bucket['_use_bys'].add(use_by)
        if production_date:
            bucket['_production_dates'].add(production_date)

    ps_ids = {
        b['product_supplier_id']
        for b in buckets.values()
        if b.get('product_supplier_id') is not None
    }
    mappings = {
        row.id: row
        for row in ProductSupplier.objects.filter(id__in=ps_ids).select_related(
            'outer_unit', 'inner_unit',
        )
    }

    out: list[dict] = []
    for bucket in buckets.values():
        qty = bucket.pop('_qty')
        qty_base = bucket.pop('_qty_base')
        lot_ids = bucket.pop('_lot_ids')
        loc_ids = bucket.pop('_loc_ids')
        use_bys = bucket.pop('_use_bys')
        production_dates = bucket.pop('_production_dates')
        product = bucket.pop('_product')
        mapping = mappings.get(bucket.get('product_supplier_id'))
        pack = supplier_pack_fields(abs(qty), product, mapping)
        use_sorted = sorted(use_bys)
        prod_sorted = sorted(production_dates)
        out.append({
            **bucket,
            'quantity': _dec(qty),
            'quantity_base': _dec(qty_base),
            'earliest_use_by': use_sorted[0] if use_sorted else None,
            'latest_use_by': use_sorted[-1] if use_sorted else None,
            'earliest_production_date': (
                prod_sorted[0] if prod_sorted else None
            ),
            'latest_production_date': (
                prod_sorted[-1] if prod_sorted else None
            ),
            'lot_count': len(lot_ids),
            'location_count': len(loc_ids),
            **pack,
        })
    out.sort(key=lambda r: (
        (r.get('product_name') or '').lower(),
        r.get('shape_format_label') or '',
        r.get('product_supplier_id') or 0,
    ))
    return out


def _day_window(
    day: date,
    from_time: time | None = None,
    to_time: time | None = None,
) -> tuple[datetime, datetime]:
    """Inclusive recorded_at window for one calendar day in the active TZ."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(day, from_time or time.min),
        tz,
    )
    end_t = to_time or time(23, 59, 59)
    if to_time is not None and to_time.second == 0 and to_time.microsecond == 0:
        end_t = to_time.replace(second=59)
    end = timezone.make_aware(datetime.combine(day, end_t), tz)
    return start, end


def _display_names(user_ids: set[int | None]) -> dict[int, str]:
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return {}
    return {
        row.id: (row.display_name or row.username or row.email or str(row.id))
        for row in RbacUser.objects.filter(id__in=ids).only(
            'id', 'display_name', 'username', 'email',
        )
    }


def operator_activity_report(
    *,
    day: date,
    from_time: time | None = None,
    to_time: time | None = None,
) -> dict:
    """
    Manager overview: who worked in a day window (recorded_at / queued_at / scanned_at).
    """
    start, end = _day_window(day, from_time, to_time)
    entries = StockEntry.objects.filter(recorded_at__gte=start, recorded_at__lte=end)
    postings = StockEntryPosting.objects.filter(queued_at__gte=start, queued_at__lte=end)
    scans = StockEntryLabelScan.objects.filter(scanned_at__gte=start, scanned_at__lte=end)

    type_rows = {
        row['entry_type']: row['c']
        for row in entries.values('entry_type').annotate(c=Count('id'))
    }
    receipts = type_rows.get(StockEntryType.RECEIPT, 0)
    receipt_scope = entries.filter(entry_type=StockEntryType.RECEIPT).aggregate(
        products=Count('lot__product_id', distinct=True),
        locations=Count('location_id', distinct=True),
    )
    posting_rows = {
        row['status']: row['c']
        for row in postings.values('status').annotate(c=Count('id'))
    }
    scan_rows = {
        row['result']: row['c']
        for row in scans.values('result').annotate(c=Count('id'))
    }
    still_queued_now = StockEntryPosting.objects.filter(
        status=StockEntryPostingStatus.QUEUED,
    ).count()

    entry_by_user = {
        row['actor_user_id']: row
        for row in entries.values('actor_user_id').annotate(
            entries=Count('id'),
            receipts=Count('id', filter=Q(entry_type=StockEntryType.RECEIPT)),
            transfers=Count(
                'id',
                filter=Q(
                    entry_type__in=[
                        StockEntryType.TRANSFER_IN,
                        StockEntryType.TRANSFER_OUT,
                    ]
                ),
            ),
        )
    }
    scan_by_user = {
        row['actor_user_id']: row['c']
        for row in scans.values('actor_user_id').annotate(c=Count('id'))
    }
    queued_now_by_user = {
        row['actor_user_id']: row['c']
        for row in StockEntryPosting.objects.filter(
            status=StockEntryPostingStatus.QUEUED,
        ).values('actor_user_id').annotate(c=Count('id'))
    }

    user_ids = set(entry_by_user) | set(scan_by_user)
    names = _display_names(user_ids)
    operators = []
    for uid in user_ids:
        erow = entry_by_user.get(uid) or {}
        operators.append({
            'user_id': uid,
            'display_name': names.get(uid) if uid is not None else 'Unknown',
            'entries': erow.get('entries', 0),
            'receipts': erow.get('receipts', 0),
            'transfers': erow.get('transfers', 0),
            'scans': scan_by_user.get(uid, 0),
            'queued': queued_now_by_user.get(uid, 0),
        })
    operators.sort(key=lambda r: (-r['entries'], -r['scans'], r['display_name'] or ''))

    named_users = sum(1 for uid in user_ids if uid is not None)
    return {
        'date': day.isoformat(),
        'from_time': (from_time or time.min).strftime('%H:%M'),
        'to_time': (to_time or time(23, 59)).strftime('%H:%M'),
        'timezone': str(timezone.get_current_timezone()),
        'summary': {
            'users': named_users,
            'entries': entries.count(),
            'receipts': receipts,
            'products': receipt_scope['products'] or 0,
            'locations': receipt_scope['locations'] or 0,
            'transfers_in': type_rows.get(StockEntryType.TRANSFER_IN, 0),
            'transfers_out': type_rows.get(StockEntryType.TRANSFER_OUT, 0),
            'scans_ok': scan_rows.get(StockEntryLabelScanResult.OK, 0),
            'scans_mismatch': scan_rows.get(StockEntryLabelScanResult.MISMATCH, 0),
            'postings_posted': posting_rows.get(StockEntryPostingStatus.POSTED, 0),
            'postings_queued': posting_rows.get(StockEntryPostingStatus.QUEUED, 0),
            'still_queued_now': still_queued_now,
        },
        'operators': operators,
    }


def operator_activity_detail(
    *,
    day: date,
    user_id: int,
    from_time: time | None = None,
    to_time: time | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict:
    """Entry lines for one operator in the day window (newest first)."""
    start, end = _day_window(day, from_time, to_time)
    row_limit = max(1, min(int(limit), _MAX_LIMIT))
    qs = (
        StockEntry.objects.filter(
            recorded_at__gte=start,
            recorded_at__lte=end,
            actor_user_id=user_id,
        )
        .select_related(*_MOVEMENT_SELECT)
        .order_by('-recorded_at', '-id')
    )
    rows = [movement_row(entry) for entry in qs[:row_limit]]
    names = _display_names({user_id})
    return {
        'date': day.isoformat(),
        'from_time': (from_time or time.min).strftime('%H:%M'),
        'to_time': (to_time or time(23, 59)).strftime('%H:%M'),
        'user_id': user_id,
        'display_name': names.get(user_id),
        'count': len(rows),
        'results': rows,
    }
