from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from locations.models import Location, LocationStockProfile
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderHistory,
    PurchaseOrderHistoryEvent,
    PurchaseOrderStatus,
)
from purchasing.services.goods_in_form import resolve_goods_in_form
from stock_ledger.models import StockLot
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util import services as stock_services


class ReleaseError(ValueError):
    pass


def is_quarantine_location(location_id: int) -> bool:
    try:
        profile = LocationStockProfile.objects.get(pk=location_id)
    except LocationStockProfile.DoesNotExist:
        return False
    return bool(profile.is_quarantine)


def require_quarantine_location(location_id: int, *, field_name: str = 'location_id') -> None:
    if not Location.objects.filter(pk=location_id).exists():
        raise ReleaseError(f'{field_name}={location_id} not found.')
    if not is_quarantine_location(location_id):
        raise ReleaseError(
            f'{field_name}={location_id} is not a quarantine location.',
        )


def require_usable_location(location_id: int, *, field_name: str = 'location_id') -> None:
    if not Location.objects.filter(pk=location_id).exists():
        raise ReleaseError(f'{field_name}={location_id} not found.')
    if is_quarantine_location(location_id):
        raise ReleaseError(
            f'{field_name}={location_id} is a quarantine location; '
            f'choose a usable storage location.',
        )


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReleaseError(f'Invalid decimal for {field_name}.') from exc
    if qty <= 0:
        raise ReleaseError(f'{field_name} must be greater than 0.')
    return qty


@transaction.atomic
def release_from_quarantine(po_id: int, *, body: dict) -> dict:
    try:
        po = PurchaseOrder.objects.select_for_update().get(pk=po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise ReleaseError('Purchase order not found.') from exc

    if po.reject_delivery:
        raise ReleaseError('Cannot release stock for a rejected delivery.')
    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
        PurchaseOrderStatus.RECEIVED,
    ):
        raise ReleaseError(
            f'Release not allowed for status={po.status}.',
        )

    from_location_id = body.get('from_location_id')
    to_location_id = body.get('to_location_id')
    if from_location_id in (None, '') or to_location_id in (None, ''):
        raise ReleaseError('from_location_id and to_location_id are required.')
    try:
        from_location_id = int(from_location_id)
        to_location_id = int(to_location_id)
    except (TypeError, ValueError) as exc:
        raise ReleaseError('Location ids must be integers.') from exc

    require_quarantine_location(from_location_id, field_name='from_location_id')
    require_usable_location(to_location_id, field_name='to_location_id')

    raw_lines = body.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ReleaseError('lines must be a non-empty list.')

    results = []
    for index, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            raise ReleaseError(f'lines[{index}] must be an object.')
        lot_id = raw.get('lot_id')
        if lot_id in (None, ''):
            raise ReleaseError(f'lines[{index}].lot_id is required.')
        try:
            lot = StockLot.objects.select_related('product').get(pk=int(lot_id))
        except (StockLot.DoesNotExist, TypeError, ValueError) as exc:
            raise ReleaseError(
                f'lines[{index}].lot_id={lot_id} not found.',
            ) from exc

        quantity = _parse_decimal(raw.get('quantity'), f'lines[{index}].quantity')
        idempotency_key = raw.get('idempotency_key')
        if idempotency_key in (None, ''):
            raise ReleaseError(f'lines[{index}].idempotency_key is required.')

        unit_id = raw.get('unit_id')
        if unit_id not in (None, ''):
            unit_id = int(unit_id)
        else:
            unit_id = lot.product.unit_id

        try:
            out_entry, in_entry = stock_services.transfer(
                idempotency_key=str(idempotency_key),
                lot=lot,
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                quantity=quantity,
                unit_id=unit_id,
                effective_at=body.get('effective_at') or timezone.now(),
                source_document_type='po',
                source_document_id=po.id,
                source_document_line=raw.get('line_no') or raw.get('source_document_line'),
                po_number=po.number,
                actor_user_id=body.get('actor_user_id') or body.get('checked_by_user_id'),
                lan_username=body.get('lan_username'),
                remarks=raw.get('remarks') or body.get('remarks') or 'QA release from quarantine',
            )
        except StockValidationError as exc:
            raise ReleaseError(str(exc)) from exc

        results.append({
            'lot_id': lot.id,
            'quantity': str(quantity),
            'transfer_out_entry_id': out_entry.id,
            'transfer_in_entry_id': in_entry.id,
            'from_location_id': from_location_id,
            'to_location_id': to_location_id,
        })

    actor = body.get('actor_user_id') or body.get('checked_by_user_id')
    if actor not in (None, ''):
        try:
            actor = int(actor)
        except (TypeError, ValueError):
            actor = None
    else:
        actor = None

    PurchaseOrderHistory.objects.create(
        purchase_order=po,
        event_type=PurchaseOrderHistoryEvent.NOTE,
        remarks=body.get('remarks') or 'QA release from quarantine',
        payload={
            'from_location_id': from_location_id,
            'to_location_id': to_location_id,
            'releases': results,
        },
        actor_user_id=actor,
    )

    form = resolve_goods_in_form(po.id)
    form['release_results'] = results
    return form
