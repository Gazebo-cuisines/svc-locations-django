from datetime import date
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from product.goods_in import effective_goods_in_type
from product.models import ProductStorageRegime, ProductTechnical
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderHistoryEvent,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.delivery import (
    DeliveryError,
    get_or_create_delivery_line,
    resolve_open_delivery,
)
from purchasing.services.goods_in_form import (
    resolve_goods_in_form,
    resolve_template,
)
from purchasing.services.qc_answers import (
    QcAnswerError,
    answer_fails,
    normalize_answer,
    parse_date,
)
from purchasing.services.qc_fail_details import (
    build_line_qc_fail_details,
    line_qc_blocked_message,
)
from purchasing.services.timeline import actor_json, record_history


class LineQcError(ValueError):
    pass


# Form footnote: chilled 0–8, frozen -18 ± 3
REGIME_TEMP_BOUNDS = {
    ProductStorageRegime.CHILLED: (Decimal('0'), Decimal('8')),
    ProductStorageRegime.FROZEN: (Decimal('-21'), Decimal('-15')),
}


def _temp_bounds(technical: ProductTechnical | None, storage_regime: str | None):
    if technical is not None:
        lo = technical.temp_check_lower_bound
        hi = technical.temp_check_upper_bound
        if lo is not None or hi is not None:
            return lo, hi
    if storage_regime in REGIME_TEMP_BOUNDS:
        return REGIME_TEMP_BOUNDS[storage_regime]
    return None, None


def _shelf_life_ok(
    *,
    use_by: date,
    delivery_date: date,
    min_days: int | None,
) -> bool:
    if min_days is None:
        return True
    remaining = (use_by - delivery_date).days
    return remaining >= int(min_days)


@transaction.atomic
def submit_line_qc(
    po_id: int,
    line_id: int,
    *,
    body: dict,
    delivery_id: int | None = None,
    actor=None,
) -> dict:
    try:
        po = (
            PurchaseOrder.objects.select_for_update(of=('self',))
            .select_related('supplier', 'ship_to_location')
            .get(pk=po_id)
        )
    except PurchaseOrder.DoesNotExist as exc:
        raise LineQcError('Purchase order not found.') from exc

    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
    ):
        raise LineQcError(
            f'Line QC only allowed when status is ordered or partial '
            f'(current={po.status}).',
        )
    try:
        delivery = resolve_open_delivery(po, delivery_id=delivery_id)
    except DeliveryError as exc:
        raise LineQcError(str(exc)) from exc
    if delivery.reject_delivery:
        raise LineQcError('Cannot run line QC on a rejected delivery.')
    if delivery.checked_at is None:
        raise LineQcError('Complete header QC before line QC.')

    try:
        line = (
            PurchaseOrderLine.objects.select_for_update(of=('self',))
            .select_related('product', 'unit', 'product_supplier')
            .get(pk=line_id, purchase_order_id=po.id)
        )
    except PurchaseOrderLine.DoesNotExist as exc:
        raise LineQcError('Purchase order line not found.') from exc

    before = {
        'delivery_id': delivery.id,
        'line_id': line.id,
        'line_no': line.line_no,
        'line_check_ok': line.line_check_ok,
        'trace_number': line.trace_number,
        'use_by': line.use_by.isoformat() if line.use_by else None,
        'line_checks': line.line_checks or {},
    }

    try:
        technical = line.product.technical
    except ObjectDoesNotExist:
        technical = None
    storage_regime = technical.storage_regime if technical else None

    template = resolve_template(
        goods_in_type=effective_goods_in_type(line.product),
        storage_regime=storage_regime,
        scope='line',
    )

    raw_answers = body.get('answers') or {}
    if not isinstance(raw_answers, dict):
        raise LineQcError('answers must be an object keyed by check code.')

    # Optional first-class shortcuts into answers
    for code in ('use_by', 'product_temperature', 'spec_check', 'production_date'):
        if code in body and code not in raw_answers:
            raw_answers[code] = body[code]

    temp_lo, temp_hi = _temp_bounds(technical, storage_regime)
    normalized = {}
    failed_codes = []
    warnings = []
    items_by_code = {}

    for item in template.items.all():
        items_by_code[item.code] = item
        if item.code not in raw_answers:
            if item.required:
                raise LineQcError(f'Answer required for {item.code}.')
            continue
        try:
            answer = normalize_answer(item, raw_answers[item.code])
        except QcAnswerError as exc:
            raise LineQcError(str(exc)) from exc
        normalized[item.code] = answer

        min_v = max_v = None
        if item.source == 'product.temp_bounds':
            min_v, max_v = temp_lo, temp_hi
            answer['bounds'] = {
                'min': str(min_v) if min_v is not None else None,
                'max': str(max_v) if max_v is not None else None,
            }

        fails = answer_fails(item, answer, min_value=min_v, max_value=max_v)
        if fails and item.is_critical:
            failed_codes.append(item.code)
        elif fails:
            warnings.append(item.code)

    delivery_date = delivery.delivery_at or po.expected_at or date.today()

    use_by = None
    if 'use_by' in normalized and normalized['use_by'].get('value'):
        try:
            use_by = parse_date(normalized['use_by']['value'], 'use_by')
        except QcAnswerError as exc:
            raise LineQcError(str(exc)) from exc

    if use_by is not None:
        try:
            acceptance = line.product.acceptance
            min_days = acceptance.min_acceptable_shelf_life_days
        except ObjectDoesNotExist:
            min_days = None
        if not _shelf_life_ok(
            use_by=use_by,
            delivery_date=delivery_date,
            min_days=min_days,
        ):
            if 'use_by' not in failed_codes:
                failed_codes.append('use_by')
            normalized.setdefault('use_by', {})['shelf_life_fail'] = True
            normalized['use_by']['min_acceptable_shelf_life_days'] = min_days

    product_temp = None
    if 'product_temperature' in normalized:
        raw_temp = normalized['product_temperature'].get('value')
        if raw_temp not in (None, ''):
            product_temp = Decimal(str(raw_temp))

    production_date = None
    if 'production_date' in normalized and normalized['production_date'].get('value'):
        try:
            production_date = parse_date(
                normalized['production_date']['value'], 'production_date',
            )
        except QcAnswerError as exc:
            raise LineQcError(str(exc)) from exc
    elif body.get('production_date') not in (None, ''):
        try:
            production_date = parse_date(body.get('production_date'), 'production_date')
        except QcAnswerError as exc:
            raise LineQcError(str(exc)) from exc

    trace_override = body.get('trace_override')
    if trace_override not in (None, ''):
        trace_number = str(trace_override).strip()
    else:
        trace_number = (
            body.get('trace_number')
            or delivery.delivery_trace_number
            or line.trace_number
        )

    line_ok = len(failed_codes) == 0
    dline = get_or_create_delivery_line(delivery, line)
    dline.line_checks = normalized
    dline.line_template_id = template.id
    dline.line_template_version = template.version
    dline.line_check_ok = line_ok
    dline.use_by = use_by
    dline.product_temperature = product_temp
    dline.production_date = production_date
    dline.trace_number = trace_number
    dline.save()

    line.line_checks = normalized
    line.line_template_id = template.id
    line.line_template_version = template.version
    line.line_check_ok = line_ok
    line.use_by = use_by
    line.product_temperature = product_temp
    line.production_date = production_date
    line.trace_number = trace_number
    line.save()

    failed_details = build_line_qc_fail_details(
        failed_codes=failed_codes,
        items_by_code=items_by_code,
        normalized=normalized,
        delivery_date=delivery_date,
    )

    event = (
        PurchaseOrderHistoryEvent.ACCEPT
        if line_ok
        else PurchaseOrderHistoryEvent.NON_CONFORMANCE
    )
    record_history(
        po=po,
        delivery=delivery,
        event_type=event,
        remarks=(
            None
            if line_ok
            else f'Line {line.line_no} QC fail: {", ".join(failed_codes)}'
        ),
        before=before,
        after={
            'delivery_id': delivery.id,
            'line_id': line.id,
            'line_no': line.line_no,
            'line_check_ok': line_ok,
            'failed_codes': failed_codes,
            'warnings': warnings,
            'failed_details': failed_details,
            'answers': normalized,
            'trace_number': trace_number,
        },
        actor=actor or actor_json(user_id=body.get('checked_by_user_id')),
    )

    form = resolve_goods_in_form(po.id, delivery_id=delivery.id)
    form['line_id'] = line.id
    form['line_check_ok'] = line_ok
    form['failed_codes'] = failed_codes
    form['warnings'] = warnings
    form['failed_details'] = failed_details
    if not line_ok:
        form['qc_blocked_message'] = line_qc_blocked_message()
    return form
