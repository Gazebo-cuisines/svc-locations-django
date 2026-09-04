from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderDeliveryLine,
    PurchaseOrderDeliveryStatus,
    PurchaseOrderHistoryEvent,
    PurchaseOrderStatus,
)
from purchasing.serialize import _iso_date, _iso_dt, _qty_str, rbac_names
from purchasing.services.timeline import actor_json, record_history


class DeliveryError(ValueError):
    pass


HEADER_FIELDS = (
    'delivery_at',
    'delivery_trace_number',
    'vehicle_temperature',
    'reject_delivery',
    'header_checks',
    'header_template_id',
    'header_template_version',
    'checked_by_user_id',
    'checked_at',
    'qc_tl_checked_by_user_id',
    'qc_tl_checked_at',
    'qc_tl_comment',
)


def open_delivery_for(po_id: int) -> PurchaseOrderDelivery | None:
    return PurchaseOrderDelivery.objects.filter(
        purchase_order_id=po_id,
        status=PurchaseOrderDeliveryStatus.OPEN,
    ).first()


def latest_delivery_for(po_id: int) -> PurchaseOrderDelivery | None:
    return (
        PurchaseOrderDelivery.objects
        .filter(purchase_order_id=po_id)
        .exclude(status=PurchaseOrderDeliveryStatus.CANCELLED)
        .order_by('-id')
        .first()
    )


def get_delivery(po_id: int, delivery_id: int) -> PurchaseOrderDelivery:
    try:
        return PurchaseOrderDelivery.objects.get(
            pk=delivery_id, purchase_order_id=po_id,
        )
    except PurchaseOrderDelivery.DoesNotExist as exc:
        raise DeliveryError('Delivery not found.') from exc


def sync_po_from_delivery(delivery: PurchaseOrderDelivery) -> None:
    po = delivery.purchase_order
    for name in HEADER_FIELDS:
        setattr(po, name, getattr(delivery, name))
    po.save(update_fields=[*HEADER_FIELDS, 'updated_at'])


def _seed_header_from_po(delivery: PurchaseOrderDelivery, po: PurchaseOrder) -> None:
    for name in HEADER_FIELDS:
        setattr(delivery, name, getattr(po, name))
    if po.reject_delivery:
        delivery.status = PurchaseOrderDeliveryStatus.REJECTED
    delivery.save(update_fields=[*HEADER_FIELDS, 'status', 'updated_at'])


def _seed_lines_from_po(delivery: PurchaseOrderDelivery, po: PurchaseOrder) -> None:
    rows = [
        PurchaseOrderDeliveryLine(
            delivery=delivery,
            po_line=line,
            qty_received=Decimal('0'),
            qty_rejected=Decimal('0'),
            production_date=line.production_date,
            use_by=line.use_by,
            trace_number=line.trace_number,
            product_temperature=line.product_temperature,
            line_checks=line.line_checks or {},
            line_template_id=line.line_template_id,
            line_template_version=line.line_template_version,
            line_check_ok=line.line_check_ok,
            last_receipt_entry_id=line.last_receipt_entry_id,
        )
        for line in po.lines.all()
    ]
    if rows:
        PurchaseOrderDeliveryLine.objects.bulk_create(rows)


def _create_locked(po: PurchaseOrder, *, seed_from_po: bool) -> PurchaseOrderDelivery:
    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
    ):
        raise DeliveryError(
            f'Delivery only allowed when status is ordered or partial '
            f'(current={po.status}).',
        )
    if not any(line.qty_balance > 0 for line in po.lines.all()):
        raise DeliveryError(
            'Purchase order is fully received; no further deliveries.',
        )
    if open_delivery_for(po.id) is not None:
        raise DeliveryError('An open delivery already exists.')
    delivery = PurchaseOrderDelivery.objects.create(
        purchase_order=po,
        status=PurchaseOrderDeliveryStatus.OPEN,
    )
    if seed_from_po:
        _seed_header_from_po(delivery, po)
        _seed_lines_from_po(delivery, po)
    return delivery


@transaction.atomic
def create_delivery(po_id: int) -> PurchaseOrderDelivery:
    try:
        po = (
            PurchaseOrder.objects.select_for_update()
            .prefetch_related('lines')
            .get(pk=po_id)
        )
    except PurchaseOrder.DoesNotExist as exc:
        raise DeliveryError('Purchase order not found.') from exc
    return _create_locked(po, seed_from_po=False)


def resolve_open_delivery(
    po: PurchaseOrder,
    *,
    delivery_id: int | None = None,
) -> PurchaseOrderDelivery:
    if delivery_id is not None:
        delivery = get_delivery(po.id, delivery_id)
        if delivery.status != PurchaseOrderDeliveryStatus.OPEN:
            raise DeliveryError(
                f'Delivery is not open (status={delivery.status}).',
            )
        return delivery
    existing = open_delivery_for(po.id)
    if existing is not None:
        return existing
    if PurchaseOrderDelivery.objects.filter(purchase_order_id=po.id).exists():
        raise DeliveryError('Create a delivery first.')
    return _create_locked(po, seed_from_po=True)


def resolve_delivery_for_receive(
    po: PurchaseOrder,
    *,
    delivery_id: int | None = None,
) -> PurchaseOrderDelivery:
    if delivery_id is not None:
        delivery = get_delivery(po.id, delivery_id)
        if (
            delivery.reject_delivery
            or delivery.status == PurchaseOrderDeliveryStatus.REJECTED
        ):
            raise DeliveryError('Cannot receive a rejected delivery.')
        return delivery
    existing = open_delivery_for(po.id)
    if existing is not None:
        return existing
    if not PurchaseOrderDelivery.objects.filter(purchase_order_id=po.id).exists():
        return _create_locked(po, seed_from_po=True)
    latest = latest_delivery_for(po.id)
    if (
        latest is not None
        and latest.status == PurchaseOrderDeliveryStatus.RECEIVED
    ):
        return latest
    raise DeliveryError('Create a delivery first.')


def get_or_create_delivery_line(
    delivery: PurchaseOrderDelivery,
    po_line,
) -> PurchaseOrderDeliveryLine:
    dline, _ = PurchaseOrderDeliveryLine.objects.get_or_create(
        delivery=delivery,
        po_line=po_line,
    )
    return dline


def delivery_list_dict(delivery: PurchaseOrderDelivery) -> dict:
    names = rbac_names({delivery.checked_by_user_id, delivery.qc_tl_checked_by_user_id})
    return {
        'id': delivery.id,
        'purchase_order_id': delivery.purchase_order_id,
        'status': delivery.status,
        'delivery_at': _iso_date(delivery.delivery_at),
        'delivery_trace_number': delivery.delivery_trace_number,
        'reject_delivery': delivery.reject_delivery,
        'vehicle_temperature': (
            _qty_str(delivery.vehicle_temperature)
            if delivery.vehicle_temperature is not None else None
        ),
        'checked_by_user_id': delivery.checked_by_user_id,
        'checked_by_name': names.get(delivery.checked_by_user_id),
        'checked_at': _iso_dt(delivery.checked_at),
        'qc_tl_checked_by_user_id': delivery.qc_tl_checked_by_user_id,
        'qc_tl_checked_by_name': names.get(delivery.qc_tl_checked_by_user_id),
        'qc_tl_checked_at': _iso_dt(delivery.qc_tl_checked_at),
        # Null unless the row came from the annotated list queryset.
        'qty_received_total': (
            _qty_str(getattr(delivery, 'qty_received_total', None) or Decimal('0'))
            if hasattr(delivery, 'qty_received_total') else None
        ),
        'line_count_received': getattr(delivery, 'line_count_received', None),
        'created_at': _iso_dt(delivery.created_at),
        'updated_at': _iso_dt(delivery.updated_at),
    }


def list_deliveries(po_id: int) -> list[PurchaseOrderDelivery]:
    if not PurchaseOrder.objects.filter(pk=po_id).exists():
        raise DeliveryError('Purchase order not found.')
    return list(
        PurchaseOrderDelivery.objects
        .filter(purchase_order_id=po_id)
        .annotate(
            qty_received_total=Sum('lines__qty_received'),
            line_count_received=Count('lines', filter=Q(lines__qty_received__gt=0)),
        )
        .order_by('id'),
    )


def list_rejected_deliveries() -> list[dict]:
    rows = (
        PurchaseOrderDelivery.objects
        .filter(status=PurchaseOrderDeliveryStatus.REJECTED)
        .select_related('purchase_order')
        .order_by('-id')
    )
    out = []
    for delivery in rows:
        item = delivery_list_dict(delivery)
        po = delivery.purchase_order
        item['purchase_order_number'] = po.number
        item['purchase_order_status'] = po.status
        out.append(item)
    return out


@transaction.atomic
def cancel_delivery(
    po_id: int,
    delivery_id: int,
    *,
    reason: str,
    actor=None,
) -> dict:
    """Abandon a truck visit that never booked anything in, so the PO can be amended."""
    reason = (reason or '').strip()
    if not reason:
        raise DeliveryError('reason is required.')

    try:
        po = PurchaseOrder.objects.select_for_update().get(pk=po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise DeliveryError('Purchase order not found.') from exc

    delivery = get_delivery(po_id, delivery_id)
    if delivery.status != PurchaseOrderDeliveryStatus.OPEN:
        raise DeliveryError(
            f'Only an open delivery can be cancelled (status={delivery.status}).',
        )
    if delivery.lines.filter(
        Q(qty_received__gt=0) | Q(qty_rejected__gt=0),
    ).exists():
        raise DeliveryError(
            'This delivery already has goods booked in; finish it instead.',
        )

    before = {'delivery_id': delivery.id, 'status': delivery.status}
    delivery.status = PurchaseOrderDeliveryStatus.CANCELLED
    delivery.save(update_fields=['status', 'updated_at'])

    record_history(
        po=po,
        delivery=delivery,
        event_type=PurchaseOrderHistoryEvent.CANCEL,
        remarks=f'Delivery cancelled: {reason}',
        before=before,
        after={
            'delivery_id': delivery.id,
            'status': delivery.status,
            'reason': reason,
        },
        actor=actor or actor_json(),
    )
    return delivery_list_dict(delivery)


@transaction.atomic
def unblock_rejected_delivery(
    po_id: int,
    delivery_id: int,
    *,
    reason: str,
    checked_by_user_id=None,
    actor=None,
) -> dict:
    reason = (reason or '').strip()
    if not reason:
        raise DeliveryError('reason is required.')

    try:
        po = PurchaseOrder.objects.select_for_update().get(pk=po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise DeliveryError('Purchase order not found.') from exc

    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
    ):
        raise DeliveryError(
            f'Unblock only allowed when PO status is ordered or partial '
            f'(current={po.status}).',
        )

    delivery = get_delivery(po_id, delivery_id)
    if delivery.status != PurchaseOrderDeliveryStatus.REJECTED:
        raise DeliveryError(
            f'Delivery is not rejected (status={delivery.status}).',
        )
    if open_delivery_for(po_id) is not None:
        raise DeliveryError(
            'An open delivery already exists; finish or reject it first.',
        )

    before = {
        'delivery_id': delivery.id,
        'status': delivery.status,
        'reject_delivery': delivery.reject_delivery,
    }

    checks = dict(delivery.header_checks or {})
    if 'reject_delivery' in checks and isinstance(checks['reject_delivery'], dict):
        checks['reject_delivery'] = {
            **checks['reject_delivery'],
            'value': False,
            'comment': reason,
        }

    now = timezone.now()
    delivery.reject_delivery = False
    delivery.status = PurchaseOrderDeliveryStatus.OPEN
    delivery.header_checks = checks
    if checked_by_user_id not in (None, ''):
        delivery.qc_tl_checked_by_user_id = int(checked_by_user_id)
        delivery.qc_tl_checked_at = now
        delivery.qc_tl_comment = reason
    delivery.save()
    sync_po_from_delivery(delivery)

    actor_payload = actor or actor_json(user_id=checked_by_user_id)
    record_history(
        po=po,
        delivery=delivery,
        event_type=PurchaseOrderHistoryEvent.ACCEPT,
        remarks=f'QC unblock: {reason}',
        before=before,
        after={
            'delivery_id': delivery.id,
            'status': delivery.status,
            'reject_delivery': False,
            'reason': reason,
        },
        actor=actor_payload,
    )
    return delivery_list_dict(delivery)
