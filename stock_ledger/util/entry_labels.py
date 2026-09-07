"""Goods IN / OUT labels keyed by stock_entry id (barcode E{id})."""

from __future__ import annotations

import re

from django.utils import timezone

from stock_ledger.models import (
    StockEntry,
    StockEntryLabel,
    StockEntryLabelFormat,
    StockEntryLabelScan,
    StockEntryLabelScanResult,
    StockEntryLabelStatus,
    StockEntryType,
)
from stock_ledger.util.conversions import StockValidationError
from users_rbac.models import RbacUser
_ENTRY_CODE = re.compile(r'^E(\d+)$', re.IGNORECASE)

LABEL_SIZES_MM = {
    StockEntryLabelFormat.PALLET: {'width_mm': 100, 'height_mm': 150},
    StockEntryLabelFormat.BOX: {'width_mm': 40, 'height_mm': 30},
}


def entry_code(entry_id: int) -> str:
    return f'E{entry_id}'


def parse_entry_code(code: str) -> int | None:
    match = _ENTRY_CODE.match((code or '').strip())
    if match is None:
        return None
    return int(match.group(1))


def scan_matches_entry_code(scanned: str, expected: str) -> bool:
    """Exact match, or scanner dropped the last character of a known label."""
    got = (scanned or '').strip().upper()
    want = (expected or '').strip().upper()
    if got == want:
        return True
    return (
        len(want) - len(got) == 1
        and want.startswith(got)
        and _ENTRY_CODE.match(got) is not None
    )


def resolve_truncated_entry_id(digits: str) -> int | None:
    """If exact E{digits} is missing, unique E{digits}{0-9} wins."""
    if not digits.isdigit():
        return None
    candidates = [int(digits + d) for d in '0123456789']
    hits = list(
        StockEntry.objects.filter(pk__in=candidates).values_list('pk', flat=True)[:2]
    )
    return hits[0] if len(hits) == 1 else None


def _size_for(label_format: str) -> dict:
    return dict(LABEL_SIZES_MM.get(label_format, LABEL_SIZES_MM[StockEntryLabelFormat.BOX]))


def get_label(entry: StockEntry) -> StockEntryLabel | None:
    try:
        return entry.label
    except StockEntryLabel.DoesNotExist:
        return None


def build_goods_in_label(entry: StockEntry, label: StockEntryLabel | None = None) -> dict:
    lot = entry.lot
    product = lot.product if lot is not None else None
    label = label if label is not None else get_label(entry)
    label_format = label.label_format if label is not None else StockEntryLabelFormat.BOX
    copies = label.label_count if label is not None else 1
    return {
        'title': 'Goods IN',
        'entry_id': entry.id,
        'entry_code': entry_code(entry.id),
        'barcode': entry_code(entry.id),
        'bcid': 'datamatrix',
        'label_format': label_format,
        'size_mm': _size_for(label_format),
        'copies': copies,
        'trace_number': lot.trace_number if lot is not None else None,
        'status': label.status if label is not None else None,
        'verified_count': label.verified_count if label is not None else 0,
        'human_readable': {
            'product_id': product.id if product is not None else None,
            'product_name': product.name if product is not None else None,
            'quantity': str(entry.quantity),
            'unit_name': entry.unit.name if entry.unit_id else None,
            'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
            'production_date': (
                lot.production_date.isoformat()
                if lot is not None and lot.production_date
                else None
            ),
            'location_id': entry.location_id,
            'po_number': entry.po_number,
        },
    }


def build_goods_out_label(
    *,
    issue_entry: StockEntry,
    source_entry: StockEntry | None = None,
    copies: int = 1,
    label: StockEntryLabel | None = None,
) -> dict:
    lot = issue_entry.lot
    product = lot.product if lot is not None else None
    label = label if label is not None else get_label(issue_entry)
    if source_entry is None and issue_entry.source_entry_id:
        source_entry = issue_entry.source_entry
    label_format = (
        label.label_format if label is not None else StockEntryLabelFormat.BOX
    )
    return {
        'title': 'Goods OUT',
        'entry_id': issue_entry.id,
        'entry_code': entry_code(issue_entry.id),
        'barcode': entry_code(issue_entry.id),
        'bcid': 'datamatrix',
        'label_format': label_format,
        'size_mm': _size_for(label_format),
        'copies': max(1, int(copies)),
        'trace_number': lot.trace_number if lot is not None else None,
        'source_entry_id': source_entry.id if source_entry is not None else None,
        'source_entry_code': (
            entry_code(source_entry.id) if source_entry is not None else None
        ),
        'status': label.status if label is not None else None,
        'verified_count': label.verified_count if label is not None else 0,
        'human_readable': {
            'product_id': product.id if product is not None else None,
            'product_name': product.name if product is not None else None,
            'quantity': str(abs(issue_entry.quantity)),
            'unit_name': issue_entry.unit.name if issue_entry.unit_id else None,
            'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
            'location_id': issue_entry.location_id,
        },
    }


def label_state_dict(label: StockEntryLabel) -> dict:
    # One hit (or zero, when scans are prefetched) instead of two COUNT round trips.
    scans = list(label.scans.all())
    ok_scans = sum(1 for scan in scans if scan.result == StockEntryLabelScanResult.OK)
    return {
        'entry_id': label.stock_entry_id,
        'entry_code': entry_code(label.stock_entry_id),
        'label_format': label.label_format,
        'label_count': label.label_count,
        'status': label.status,
        'verified_count': label.verified_count,
        'printed_count': label.printed_count,
        'scan_count': len(scans),
        'ok_scan_count': ok_scans,
        'printed_at': label.printed_at.isoformat() if label.printed_at else None,
        'verified_at': label.verified_at.isoformat() if label.verified_at else None,
        'meta': label.meta or {},
    }


def scan_event_dict(scan: StockEntryLabelScan, *, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        'id': scan.id,
        'entry_id': scan.stock_entry_id,
        'label_id': scan.label_id,
        'scanned_at': scan.scanned_at.isoformat() if scan.scanned_at else None,
        'code': scan.code,
        'result': scan.result,
        'actor_user_id': scan.actor_user_id,
        'actor_name': names.get(scan.actor_user_id) if scan.actor_user_id else None,
        'lan_username': scan.lan_username,
        'source_workstation': scan.source_workstation,
        'meta': scan.meta or {},
    }


def _record_scan(
    *,
    label: StockEntryLabel,
    code: str,
    result: str,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
    meta=None,
) -> StockEntryLabelScan:
    return StockEntryLabelScan.objects.create(
        label=label,
        stock_entry_id=label.stock_entry_id,
        scanned_at=timezone.now(),
        code=(code or '')[:64],
        result=result,
        actor_user_id=actor_user_id,
        lan_username=lan_username,
        source_workstation=source_workstation,
        meta=meta or {},
    )

def create_entry_label(
    *,
    entry: StockEntry,
    label_format: str,
    label_count: int | None = None,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
) -> StockEntryLabel:
    fmt = str(label_format or '').strip().lower()
    if fmt not in (
        StockEntryLabelFormat.PALLET,
        StockEntryLabelFormat.BOX,
    ):
        raise StockValidationError(
            'label_format must be pallet or box.',
        )
    if label_count in (None, ''):
        count = 1 if fmt == StockEntryLabelFormat.PALLET else None
    else:
        try:
            count = int(label_count)
        except (TypeError, ValueError) as exc:
            raise StockValidationError('label_count must be an integer.') from exc
    if count is None:
        raise StockValidationError(
            'label_count is required when label_format=box.',
        )
    if count < 1:
        raise StockValidationError('label_count must be >= 1.')
    # pallet label_count = print copies of the same barcode (not N stock rows).
    existing = StockEntryLabel.objects.filter(stock_entry=entry).first()
    if existing is not None:
        return existing
    return StockEntryLabel.objects.create(
        stock_entry=entry,
        label_format=fmt,
        label_count=count,
        status=StockEntryLabelStatus.PENDING,
        actor_user_id=actor_user_id,
        lan_username=lan_username,
        source_workstation=source_workstation,
    )


def mark_printed(
    *,
    entry_id: int,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
) -> StockEntryLabel:
    """Mark printed (or reprint damaged sticker). Status stays verified if already verified."""
    label = (
        StockEntryLabel.objects
        .select_related('stock_entry__lot__product', 'stock_entry__unit')
        .filter(stock_entry_id=entry_id)
        .first()
    )
    if label is None:
        raise StockValidationError(
            f'No label record for entry_id={entry_id}. '
            f'Pass label_format on receive first.',
        )
    update_fields = ['printed_count', 'printed_at']
    if label.status == StockEntryLabelStatus.PENDING:
        label.status = StockEntryLabelStatus.PRINTED
        update_fields.append('status')
    label.printed_count = label.printed_count + 1
    label.printed_at = timezone.now()
    if actor_user_id is not None:
        label.actor_user_id = actor_user_id
        update_fields.append('actor_user_id')
    if lan_username is not None:
        label.lan_username = lan_username
        update_fields.append('lan_username')
    if source_workstation is not None:
        label.source_workstation = source_workstation
        update_fields.append('source_workstation')
    label.save(update_fields=update_fields)
    return label


def verify_label(
    *,
    entry_id: int,
    code: str,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
    meta=None,
) -> dict:
    label = (
        StockEntryLabel.objects
        .select_related('stock_entry__lot__product', 'stock_entry__unit')
        .filter(stock_entry_id=entry_id)
        .first()
    )
    if label is None:
        raise StockValidationError(
            f'No label record for entry_id={entry_id}.',
        )
    expected = entry_code(entry_id)
    scanned = (code or '').strip().upper()
    actor_kwargs = {
        'actor_user_id': actor_user_id,
        'lan_username': lan_username,
        'source_workstation': source_workstation,
        'meta': meta if isinstance(meta, dict) else {},
    }
    if not scan_matches_entry_code(scanned, expected):
        scan = _record_scan(
            label=label,
            code=code,
            result=StockEntryLabelScanResult.MISMATCH,
            **actor_kwargs,
        )
        raise StockValidationError(
            f'Label mismatch: expected {expected}, got {code!r}. '
            f'scan_id={scan.id}',
        )

    scan = _record_scan(
        label=label,
        code=code,
        result=StockEntryLabelScanResult.OK,
        **actor_kwargs,
    )
    # Count every successful scan (re-scans included); gate still uses label_count.
    label.verified_count = label.verified_count + 1
    update_fields = ['verified_count']
    if label.verified_count >= label.label_count:
        label.status = StockEntryLabelStatus.VERIFIED
        label.verified_at = timezone.now()
        update_fields.extend(['status', 'verified_at'])
    elif label.status == StockEntryLabelStatus.PENDING:
        label.status = StockEntryLabelStatus.PRINTED
        if label.printed_at is None:
            label.printed_at = timezone.now()
            update_fields.append('printed_at')
        update_fields.append('status')
    label.save(update_fields=update_fields)
    return {
        'matched': True,
        'expected_code': expected,
        'scan': scan_event_dict(scan),
        'label': label_state_dict(label),
        'goods_in_label': build_goods_in_label(label.stock_entry, label),
    }


def list_label_activity(entry_id: int) -> dict:
    label = (
        StockEntryLabel.objects
        .filter(stock_entry_id=entry_id)
        .first()
    )
    if label is None:
        raise StockValidationError(
            f'No label record for entry_id={entry_id}.',
        )
    scans = list(
        StockEntryLabelScan.objects
        .filter(stock_entry_id=entry_id)
        .order_by('-scanned_at', '-id')
    )
    user_ids = {s.actor_user_id for s in scans if s.actor_user_id is not None}
    names = {
        u.id: (u.display_name or u.username)
        for u in RbacUser.objects.filter(pk__in=user_ids).only(
            'id', 'display_name', 'username',
        )
    }
    return {
        'entry_id': entry_id,
        'entry_code': entry_code(entry_id),
        'label': label_state_dict(label),
        'scan_count': len(scans),
        'ok_scan_count': sum(
            1 for s in scans if s.result == StockEntryLabelScanResult.OK
        ),
        'mismatch_count': sum(
            1 for s in scans if s.result == StockEntryLabelScanResult.MISMATCH
        ),
        'scans': [scan_event_dict(s, names=names) for s in scans],
    }


def get_entry_for_label(entry_id: int) -> StockEntry:
    entry = (
        StockEntry.objects
        .select_related('lot__product', 'unit', 'location', 'label')
        .filter(pk=entry_id)
        .first()
    )
    if entry is None:
        raise StockValidationError(f'entry_id={entry_id} not found')
    return entry


def require_receipt_entry(entry: StockEntry) -> None:
    if entry.entry_type != StockEntryType.RECEIPT:
        raise StockValidationError(
            f'entry_id={entry.id} is {entry.entry_type}, expected receipt.',
        )
