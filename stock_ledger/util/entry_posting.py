"""Queue / post goods-in receipts so stock hits balance only after label confirm."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from stock_ledger.models import (
    StockEntry,
    StockEntryLabelStatus,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util import entry_labels
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.services import _project_balance
from purchasing.services.po_qty import apply_po_receipt_from_entry


def posting_dict(posting: StockEntryPosting) -> dict:
    return {
        'entry_id': posting.stock_entry_id,
        'entry_code': entry_labels.entry_code(posting.stock_entry_id),
        'status': posting.status,
        'queued_at': posting.queued_at.isoformat() if posting.queued_at else None,
        'posted_at': posting.posted_at.isoformat() if posting.posted_at else None,
        'cancelled_at': (
            posting.cancelled_at.isoformat() if posting.cancelled_at else None
        ),
        'meta': posting.meta or {},
    }


def get_posting(entry: StockEntry) -> StockEntryPosting | None:
    try:
        return entry.posting
    except StockEntryPosting.DoesNotExist:
        return None


def queue_entry(
    *,
    entry: StockEntry,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
    meta: dict | None = None,
) -> StockEntryPosting:
    existing = StockEntryPosting.objects.filter(stock_entry=entry).first()
    if existing is not None:
        return existing
    return StockEntryPosting.objects.create(
        stock_entry=entry,
        status=StockEntryPostingStatus.QUEUED,
        queued_at=timezone.now(),
        actor_user_id=actor_user_id,
        lan_username=lan_username,
        source_workstation=source_workstation,
        meta=meta or {},
    )


def cancel_entry(*, entry_id: int) -> StockEntryPosting:
    posting = (
        StockEntryPosting.objects
        .filter(stock_entry_id=entry_id)
        .first()
    )
    if posting is None:
        raise StockValidationError(f'entry_id={entry_id} has no posting.')
    if posting.status == StockEntryPostingStatus.POSTED:
        raise StockValidationError(f'entry_id={entry_id} is already posted.')
    if posting.status != StockEntryPostingStatus.CANCELLED:
        posting.status = StockEntryPostingStatus.CANCELLED
        posting.cancelled_at = timezone.now()
        posting.save(update_fields=['status', 'cancelled_at'])
    return posting


@transaction.atomic
def post_entry(
    *,
    entry_id: int,
    require_label_verified: bool = True,
    actor_user_id=None,
    lan_username=None,
    source_workstation=None,
) -> dict:
    entry = (
        StockEntry.objects
        .select_for_update()
        .select_related('lot__product', 'unit', 'location', 'label', 'posting')
        .filter(pk=entry_id)
        .first()
    )
    if entry is None:
        raise StockValidationError(f'entry_id={entry_id} not found')
    if entry.entry_type not in (
        StockEntryType.RECEIPT,
        StockEntryType.TRANSFER_OUT,
    ):
        raise StockValidationError(
            f'entry_id={entry_id} is {entry.entry_type}, '
            f'expected receipt or transfer_out.',
        )

    posting = get_posting(entry)
    if posting is None:
        # Legacy receipt already projected balance at create time.
        return {
            'entry_id': entry.id,
            'entry_code': entry_labels.entry_code(entry.id),
            'status': StockEntryPostingStatus.POSTED,
            'already_live': True,
            'posting': None,
        }

    posting = (
        StockEntryPosting.objects
        .select_for_update()
        .select_related('stock_entry')
        .get(pk=posting.pk)
    )
    if posting.status == StockEntryPostingStatus.POSTED:
        return {
            'entry_id': entry.id,
            'entry_code': entry_labels.entry_code(entry.id),
            'status': posting.status,
            'already_live': True,
            'posting': posting_dict(posting),
        }
    if posting.status == StockEntryPostingStatus.CANCELLED:
        raise StockValidationError(
            f'entry_id={entry_id} posting is cancelled.',
        )

    if require_label_verified:
        label = entry_labels.get_label(entry)
        if label is None:
            raise StockValidationError(
                f'entry_id={entry_id} has no label; print/verify before post.',
            )
        if label.status != StockEntryLabelStatus.VERIFIED:
            raise StockValidationError(
                f'entry_id={entry_id} label status={label.status}; '
                f'verify all copies before posting stock '
                f'({label.verified_count}/{label.label_count}).',
            )

    _project_balance(entry=entry, override_reason=entry.override_reason)
    if entry.entry_type == StockEntryType.TRANSFER_OUT:
        pair = (
            StockEntry.objects
            .select_for_update()
            .filter(
                transfer_group_id=entry.transfer_group_id,
                entry_type=StockEntryType.TRANSFER_IN,
            )
            .exclude(pk=entry.pk)
            .first()
        )
        if pair is None:
            raise StockValidationError(
                f'entry_id={entry_id} has no paired transfer_in.',
            )
        _project_balance(entry=pair, override_reason=pair.override_reason)
    posting.status = StockEntryPostingStatus.POSTED
    posting.posted_at = timezone.now()
    if actor_user_id is not None:
        posting.actor_user_id = actor_user_id
    if lan_username is not None:
        posting.lan_username = lan_username
    if source_workstation is not None:
        posting.source_workstation = source_workstation
    posting.save(
        update_fields=[
            'status',
            'posted_at',
            'actor_user_id',
            'lan_username',
            'source_workstation',
        ],
    )
    apply_po_receipt_from_entry(entry)
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'status': posting.status,
        'already_live': False,
        'posting': posting_dict(posting),
        'entry': None,  # filled by view with entry_dict
    }


def list_queued_receipts(*, limit: int = 100) -> list[StockEntry]:
    limit = max(1, min(int(limit), 500))
    return list(
        StockEntry.objects
        .select_related(
            'lot__product', 'unit', 'location', 'label', 'posting',
            'counterparty_location',
        )
        .filter(
            entry_type=StockEntryType.RECEIPT,
            posting__status=StockEntryPostingStatus.QUEUED,
        )
        .order_by('posting__queued_at', 'id')[:limit]
    )
