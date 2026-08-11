from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from product.models import ProductTechnical
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderHistory,
    PurchaseOrderHistoryEvent,
    PurchaseOrderStatus,
)
from purchasing.services.goods_in_form import (
    _header_key,
    resolve_goods_in_form,
    resolve_template,
)
from purchasing.services.julian import julian_trace_number
from purchasing.services.qc_answers import (
    QcAnswerError,
    answer_fails,
    normalize_answer,
    parse_date,
)


class HeaderQcError(ValueError):
    pass


@transaction.atomic
def submit_header_qc(po_id: int, *, body: dict) -> dict:
    try:
        po = (
            PurchaseOrder.objects.select_for_update()
            .select_related('supplier', 'ship_to_location')
            .prefetch_related('lines__product')
            .get(pk=po_id)
        )
    except PurchaseOrder.DoesNotExist as exc:
        raise HeaderQcError('Purchase order not found.') from exc

    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
    ):
        raise HeaderQcError(
            f'Header QC only allowed when status is ordered or partial '
            f'(current={po.status}).',
        )

    lines = list(po.lines.all())
    tech_by_product = {
        row.product_id: row
        for row in ProductTechnical.objects.filter(
            product_id__in=[line.product_id for line in lines],
        )
    }
    header_type, header_regime = _header_key(lines, tech_by_product)
    template = resolve_template(
        goods_in_type=header_type,
        storage_regime=header_regime,
        scope='header',
    )

    raw_answers = body.get('answers') or {}
    if not isinstance(raw_answers, dict):
        raise HeaderQcError('answers must be an object keyed by check code.')

    normalized = {}
    failed_codes = []
    for item in template.items.all():
        if item.code not in raw_answers:
            if item.required:
                raise HeaderQcError(f'Answer required for {item.code}.')
            continue
        try:
            answer = normalize_answer(item, raw_answers[item.code])
        except QcAnswerError as exc:
            raise HeaderQcError(str(exc)) from exc
        normalized[item.code] = answer
        if item.is_critical and answer_fails(item, answer):
            failed_codes.append(item.code)

    try:
        delivery_date = parse_date(
            body.get('delivery_date')
            or po.delivery_at
            or po.expected_at
            or date.today(),
            'delivery_date',
        )
    except QcAnswerError as exc:
        raise HeaderQcError(str(exc)) from exc

    trace_override = body.get('trace_override')
    override_reason = (body.get('override_reason') or '').strip() or None
    if trace_override not in (None, ''):
        if not override_reason:
            raise HeaderQcError(
                'override_reason is required when trace_override is set.',
            )
        trace_number = str(trace_override).strip()
    else:
        trace_number = julian_trace_number(delivery_date)

    reject = bool(failed_codes) or (
        normalized.get('reject_delivery', {}).get('value') is True
    )

    vehicle_temp = None
    if 'vehicle_temperature' in normalized:
        raw_temp = normalized['vehicle_temperature'].get('value')
        if raw_temp not in (None, ''):
            vehicle_temp = Decimal(str(raw_temp))

    now = timezone.now()
    checked_by = body.get('checked_by_user_id')
    if checked_by in (None, ''):
        raise HeaderQcError('checked_by_user_id is required.')
    try:
        checked_by = int(checked_by)
    except (TypeError, ValueError) as exc:
        raise HeaderQcError('checked_by_user_id must be an integer.') from exc

    po.delivery_at = delivery_date
    po.delivery_trace_number = trace_number
    po.vehicle_temperature = vehicle_temp
    po.reject_delivery = reject
    po.header_checks = normalized
    po.header_template_id = template.id
    po.header_template_version = template.version
    po.checked_by_user_id = checked_by
    po.checked_at = now

    qc_tl = body.get('qc_tl_checked_by_user_id')
    if qc_tl not in (None, ''):
        po.qc_tl_checked_by_user_id = int(qc_tl)
        po.qc_tl_checked_at = now
        po.qc_tl_comment = body.get('qc_tl_comment') or None
    elif body.get('qc_tl_comment'):
        po.qc_tl_comment = body.get('qc_tl_comment')

    if reject and body.get('remarks'):
        po.remarks = str(body.get('remarks'))[:256]

    po.save()

    event = (
        PurchaseOrderHistoryEvent.REJECT
        if reject
        else PurchaseOrderHistoryEvent.ACCEPT
    )
    remarks_parts = []
    if failed_codes:
        remarks_parts.append(f'Critical fails: {", ".join(failed_codes)}')
    if override_reason:
        remarks_parts.append(f'Trace override: {override_reason}')
    if body.get('remarks'):
        remarks_parts.append(str(body.get('remarks')))

    PurchaseOrderHistory.objects.create(
        purchase_order=po,
        event_type=event,
        remarks='; '.join(remarks_parts) or None,
        payload={
            'delivery_date': delivery_date.isoformat(),
            'delivery_trace_number': trace_number,
            'failed_codes': failed_codes,
            'trace_override': bool(trace_override),
            'answers': normalized,
        },
        actor_user_id=checked_by,
    )

    return resolve_goods_in_form(po.id)
