"""How much stock is left on one goods-in sticker (barcode E{stock_entry.id})."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from stock_ledger.models import (
    StockEntry,
    StockEntryLabel,
    StockEntryLabelStatus,
    StockEntryPostingStatus,
    StockLot,
)
from stock_ledger.util import entry_labels
from stock_ledger.util.conversions import StockValidationError
from users_rbac.models import RbacUser

ZERO = Decimal('0')


def drawn_from_entry(entry: StockEntry) -> Decimal:
    """Stock already picked against this sticker, as a positive number.

    Queued draws count: a bag committed to a pick must not read as full on
    re-scan. Cancelled postings and reversed draws release their stock again.
    """
    total = (
        StockEntry.objects
        .filter(source_entry_id=entry.pk, reversed_by__isnull=True)
        .exclude(posting__status=StockEntryPostingStatus.CANCELLED)
        .aggregate(total=Sum('quantity'))
        ['total']
    )
    return abs(total) if total is not None else ZERO


def remaining_for_entry(
    entry: StockEntry,
    *,
    lot_quantity: Decimal | None = None,
) -> Decimal:
    """Sticker quantity minus what has been drawn from it.

    Capped at ``lot_quantity`` when given, so a sticker never promises more
    than the lot actually holds at that location — by-lot picks and count
    adjustments name no sticker and would otherwise leave it reading full.
    """
    remaining = abs(entry.quantity) - drawn_from_entry(entry)
    if remaining < ZERO:
        remaining = ZERO
    if lot_quantity is not None:
        cap = Decimal(str(lot_quantity))
        if cap < remaining:
            return cap
    return remaining


def check_draw(
    *,
    source_entry: StockEntry,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    lot_quantity: Decimal | None = None,
) -> None:
    """Reject a pick that does not match the scanned sticker."""
    code = entry_labels.entry_code(source_entry.id)
    if source_entry.lot_id != lot.id:
        raise StockValidationError(
            f'{code} is a label for batch {source_entry.lot.trace_number}, '
            f'not {lot.trace_number}.'
        )
    if source_entry.location_id != location_id:
        raise StockValidationError(
            f'{code} is at {source_entry.location.name}, not this location.'
        )
    remaining = remaining_for_entry(source_entry, lot_quantity=lot_quantity)
    if quantity > remaining:
        unit_name = source_entry.unit.name if source_entry.unit_id else 'stock'
        raise StockValidationError(
            f'Only {remaining} {unit_name} left on {code} (need {quantity}). '
            f'Scan another label or send {remaining} or less.'
        )


def _post_blocker(draw: StockEntry, label: StockEntryLabel | None) -> tuple[str, str]:
    """What still stands between a queued pick and the stock actually moving."""
    code = entry_labels.entry_code(draw.id)
    if label is None:
        return (
            'label_not_created',
            f'Print the goods-out label for {code}, verify it, then post {code}.',
        )
    if label.status == StockEntryLabelStatus.PENDING:
        return (
            'label_not_printed',
            f'Print label {code} and verify it, then post {code}.',
        )
    if label.status != StockEntryLabelStatus.VERIFIED:
        return (
            'labels_not_verified',
            f'{label.verified_count} of {label.label_count} labels verified on '
            f'{code}. Scan the rest, then post {code}.',
        )
    return 'ready_to_post', f'Post {code} to move the stock.'


def queued_draws_for_entry(entry: StockEntry) -> list[dict]:
    """Picks on this sticker whose stock has not reached the balance yet.

    Explains the gap between the sticker figure and the lot balance: who
    committed the stock, when, and what is left to do before it posts.
    """
    draws = list(
        StockEntry.objects
        .filter(
            source_entry_id=entry.pk,
            reversed_by__isnull=True,
            posting__status=StockEntryPostingStatus.QUEUED,
        )
        .select_related('posting', 'counterparty_location')
        .order_by('id')
    )
    actor_ids = {
        draw.posting.actor_user_id
        for draw in draws
        if draw.posting.actor_user_id is not None
    }
    names = {
        user.id: (user.display_name or user.username)
        for user in RbacUser.objects.filter(pk__in=actor_ids).only(
            'id', 'display_name', 'username',
        )
    }

    rows = []
    for draw in draws:
        posting = draw.posting
        label = entry_labels.get_label(draw)
        blocked_by, next_step = _post_blocker(draw, label)
        rows.append({
            'entry_id': draw.id,
            'entry_code': entry_labels.entry_code(draw.id),
            'entry_type': draw.entry_type,
            'quantity': abs(draw.quantity),
            'to_location_id': draw.counterparty_location_id,
            'to_location_name': (
                draw.counterparty_location.name
                if draw.counterparty_location_id
                else None
            ),
            'queued_at': posting.queued_at.isoformat() if posting.queued_at else None,
            'queued_by': names.get(posting.actor_user_id) or posting.lan_username,
            'actor_user_id': posting.actor_user_id,
            'lan_username': posting.lan_username,
            'source_workstation': posting.source_workstation,
            'label_status': label.status if label is not None else None,
            'label_count': label.label_count if label is not None else None,
            'verified_count': label.verified_count if label is not None else None,
            'blocked_by': blocked_by,
            'next_step': next_step,
            'post_endpoint': f'/stock/entries/{draw.id}/post/',
        })
    return rows
