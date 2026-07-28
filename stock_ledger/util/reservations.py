from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockLot,
    StockReservation,
    StockReservationStatus,
)
from stock_ledger.util.conversions import StockValidationError


def open_reserved_quantity(*, lot_id: int, location_id: int) -> Decimal:
    total = (
        StockReservation.objects
        .filter(
            lot_id=lot_id,
            location_id=location_id,
            status=StockReservationStatus.OPEN,
        )
        .aggregate(total=Sum('quantity'))
        ['total']
    )
    return total or Decimal('0')


def available_to_promise(*, lot_id: int, location_id: int) -> Decimal:
    """ATP = on-hand balance minus open reservations."""
    balance = (
        StockBalance.objects
        .filter(lot_id=lot_id, location_id=location_id)
        .values_list('quantity', flat=True)
        .first()
    )
    on_hand = balance if balance is not None else Decimal('0')
    return on_hand - open_reserved_quantity(lot_id=lot_id, location_id=location_id)


def reserve(
    *,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int,
    source_document_type: str | None = None,
    source_document_id: int | None = None,
    source_document_line: int | None = None,
    expires_at=None,
    allow_over_reserve: bool = False,
) -> StockReservation:
    if quantity <= 0:
        raise StockValidationError('reservation quantity must be positive')

    with transaction.atomic():
        if not allow_over_reserve:
            atp = available_to_promise(lot_id=lot.id, location_id=location_id)
            if quantity > atp:
                raise StockValidationError(
                    f'reservation quantity {quantity} exceeds ATP {atp}'
                )
        return StockReservation.objects.create(
            lot=lot,
            location_id=location_id,
            quantity=quantity,
            unit_id=unit_id,
            status=StockReservationStatus.OPEN,
            source_document_type=source_document_type,
            source_document_id=source_document_id,
            source_document_line=source_document_line,
            expires_at=expires_at,
        )


def _require_open(reservation: StockReservation) -> StockReservation:
    locked = (
        StockReservation.objects
        .select_for_update()
        .get(pk=reservation.pk)
    )
    if locked.status != StockReservationStatus.OPEN:
        raise StockValidationError(
            f'reservation {locked.id} is {locked.status}, expected open'
        )
    return locked


@transaction.atomic
def release(reservation: StockReservation) -> StockReservation:
    locked = _require_open(reservation)
    locked.status = StockReservationStatus.RELEASED
    locked.save(update_fields=['status', 'updated_at'])
    return locked


@transaction.atomic
def consume(
    reservation: StockReservation,
    *,
    entry: StockEntry,
) -> StockReservation:
    locked = _require_open(reservation)
    if entry.lot_id != locked.lot_id or entry.location_id != locked.location_id:
        raise StockValidationError(
            'consume entry lot/location must match reservation'
        )
    locked.status = StockReservationStatus.CONSUMED
    locked.consumed_by_entry = entry
    locked.save(update_fields=['status', 'consumed_by_entry', 'updated_at'])
    return locked


@transaction.atomic
def expire(reservation: StockReservation) -> StockReservation:
    locked = _require_open(reservation)
    locked.status = StockReservationStatus.EXPIRED
    locked.save(update_fields=['status', 'updated_at'])
    return locked


def expire_due(now=None) -> int:
    """Mark open reservations past expires_at as expired. Returns count."""
    now = now or timezone.now()
    count = 0
    qs = (
        StockReservation.objects
        .filter(
            status=StockReservationStatus.OPEN,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        .order_by('id')
    )
    for reservation in qs.iterator():
        with transaction.atomic():
            locked = (
                StockReservation.objects
                .select_for_update()
                .filter(pk=reservation.pk, status=StockReservationStatus.OPEN)
                .first()
            )
            if locked is None:
                continue
            locked.status = StockReservationStatus.EXPIRED
            locked.save(update_fields=['status', 'updated_at'])
            count += 1
    return count
