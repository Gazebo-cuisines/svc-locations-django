from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from stock_ledger.models import StockBalance, StockLot, StockReservationStatus
from stock_ledger.util import reservations as stock_reservations
from stock_ledger.util.conversions import StockValidationError

from planning.adapters.types import CONTRACT_VERSION, LotSpec, SupplySpec
from planning.errors import PlanningError
from planning.models import PlanAllocation, PlanStatus, PlanSupply

__all__ = [
    'CONTRACT_VERSION',
    'lots_for_product',
    'projected_supply',
    'soft_allocated_quantity',
    'create_stock_reservation',
    'release_stock_reservation',
]


def lots_for_product(product_id: int, location_ids: list[int]) -> list[LotSpec]:
    if not location_ids:
        return []
    balances = (
        StockBalance.objects
        .filter(lot__product_id=product_id, location_id__in=location_ids)
        .select_related('lot')
    )
    result: list[LotSpec] = []
    for bal in balances:
        lot: StockLot = bal.lot
        atp = stock_reservations.available_to_promise(
            lot_id=lot.id,
            location_id=bal.location_id,
        )
        result.append(
            LotSpec(
                lot_id=lot.id,
                product_id=product_id,
                quantity_on_hand=bal.quantity,
                location_id=bal.location_id,
                use_by=lot.use_by,
                production_date=lot.production_date,
                atp=atp,
            )
        )
    return result


def projected_supply(
    product_id: int,
    location_ids: list[int],
    *,
    need_by: date,
) -> list[SupplySpec]:
    if not location_ids:
        return []
    end = datetime.combine(need_by, datetime.max.time())
    if timezone.is_aware(timezone.now()):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    rows = PlanSupply.objects.filter(
        product_id=product_id,
        location_id__in=location_ids,
        expected_at__lte=end,
    )
    return [
        SupplySpec(
            id=r.id,
            product_id=r.product_id,
            location_id=r.location_id,
            expected_at=r.expected_at,
            quantity=r.quantity,
        )
        for r in rows
    ]


def soft_allocated_quantity(product_id: int, location_ids: list[int]) -> Decimal:
    """Soft plan allocations not yet hardened to stock_reservation."""
    if not location_ids:
        return Decimal('0')
    total = (
        PlanAllocation.objects
        .filter(
            requirement__product_id=product_id,
            location_id__in=location_ids,
            stock_reservation__isnull=True,
            requirement__run__plan__status__in=[
                PlanStatus.DRAFT,
                PlanStatus.LOCKED,
            ],
        )
        .aggregate(total=Sum('quantity'))
        ['total']
    )
    return total or Decimal('0')


def create_stock_reservation(
    *,
    lot_id: int,
    location_id: int,
    quantity: Decimal,
    unit_id: int,
    plan_id: int,
    requirement_id: int,
):
    try:
        lot = StockLot.objects.get(pk=lot_id)
        return stock_reservations.reserve(
            lot=lot,
            location_id=location_id,
            quantity=quantity,
            unit_id=unit_id,
            source_document_type='plan',
            source_document_id=plan_id,
            source_document_line=requirement_id,
            allow_over_reserve=False,
        )
    except StockValidationError as exc:
        raise PlanningError(str(exc)) from exc
    except StockLot.DoesNotExist as exc:
        raise PlanningError(f'lot {lot_id} not found') from exc


def release_stock_reservation(reservation_id: int) -> None:
    from stock_ledger.models import StockReservation

    try:
        row = StockReservation.objects.get(pk=reservation_id)
    except StockReservation.DoesNotExist:
        return
    if row.status != StockReservationStatus.OPEN:
        return
    try:
        stock_reservations.release(row)
    except StockValidationError:
        return
