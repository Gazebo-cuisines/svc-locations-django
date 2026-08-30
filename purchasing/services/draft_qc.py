from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from product.goods_in import effective_goods_in_type
from product.models import ProductTechnical
from purchasing.models import (
    AdhocGoodsInSession,
    AdhocGoodsInStatus,
    GoodsInCheckScope,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.adhoc_goods_in import (
    AdhocGoodsInError,
    _load_session,
    session_form_dict,
)
from purchasing.services.delivery import (
    DeliveryError,
    get_or_create_delivery_line,
    resolve_open_delivery,
    sync_po_from_delivery,
)
from purchasing.services.goods_in_form import (
    GoodsInFormError,
    _header_key,
    resolve_goods_in_form,
    resolve_template,
)
from purchasing.services.header_qc import HeaderQcError
from purchasing.services.julian import julian_trace_number
from purchasing.services.line_qc import LineQcError
from purchasing.services.qc_answer_store import load_answers, upsert_answers
from purchasing.services.qc_lock import claim_lock
from purchasing.services.qc_answers import QcAnswerError, normalize_answer, parse_date


def _actor_id(body: dict) -> int:
    raw = body.get('checked_by_user_id')
    if raw in (None, ''):
        raise ValueError('checked_by_user_id is required.')
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('checked_by_user_id must be an integer.') from exc


def _partial_answers(template, raw_answers) -> tuple[dict, dict]:
    if not isinstance(raw_answers, dict) or not raw_answers:
        raise ValueError('answers must be an object with at least one check.')
    items_by_code = {item.code: item for item in template.items.all()}
    normalized = {}
    for code, raw in raw_answers.items():
        item = items_by_code.get(code)
        if item is None:
            raise ValueError(f'Unknown check code: {code}.')
        normalized[code] = normalize_answer(item, raw, draft=True)
    return normalized, items_by_code


def _merge_checks(existing: dict, from_table: dict) -> dict:
    out = dict(existing or {})
    out.update(from_table)
    return out


@transaction.atomic
def draft_header_qc(
    po_id: int,
    *,
    body: dict,
    delivery_id: int | None = None,
) -> dict:
    try:
        po = (
            PurchaseOrder.objects.select_for_update(of=('self',))
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
    try:
        delivery = resolve_open_delivery(po, delivery_id=delivery_id)
    except DeliveryError as exc:
        raise HeaderQcError(str(exc)) from exc
    if delivery.checked_at is not None:
        raise HeaderQcError('Header QC already submitted. Use POST to change it.')

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
        scope=GoodsInCheckScope.HEADER,
    )
    try:
        checked_by = _actor_id(body)
        normalized, items_by_code = _partial_answers(template, body.get('answers'))
    except (ValueError, QcAnswerError) as exc:
        raise HeaderQcError(str(exc)) from exc

    claim_lock(delivery, checked_by, noun='delivery')

    if body.get('delivery_date') not in (None, ''):
        try:
            delivery.delivery_at = parse_date(body.get('delivery_date'), 'delivery_date')
        except QcAnswerError as exc:
            raise HeaderQcError(str(exc)) from exc
        if not delivery.delivery_trace_number:
            delivery.delivery_trace_number = julian_trace_number(delivery.delivery_at)

    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=checked_by,
        scope=GoodsInCheckScope.HEADER,
        delivery=delivery,
    )
    delivery.header_checks = _merge_checks(
        delivery.header_checks,
        load_answers(delivery=delivery),
    )
    delivery.header_template_id = template.id
    delivery.header_template_version = template.version
    delivery.save()
    sync_po_from_delivery(delivery)
    return resolve_goods_in_form(po.id, delivery_id=delivery.id)


@transaction.atomic
def draft_line_qc(
    po_id: int,
    line_id: int,
    *,
    body: dict,
    delivery_id: int | None = None,
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
    try:
        line = (
            PurchaseOrderLine.objects.select_for_update(of=('self',))
            .select_related('product', 'unit')
            .get(pk=line_id, purchase_order_id=po.id)
        )
    except PurchaseOrderLine.DoesNotExist as exc:
        raise LineQcError('Purchase order line not found.') from exc
    dline = get_or_create_delivery_line(delivery, line)
    if dline.line_check_ok:
        raise LineQcError('Line QC already submitted. Use POST to change it.')

    try:
        technical = line.product.technical
    except ObjectDoesNotExist:
        technical = None
    storage_regime = technical.storage_regime if technical else None
    template = resolve_template(
        goods_in_type=effective_goods_in_type(line.product),
        storage_regime=storage_regime,
        scope=GoodsInCheckScope.LINE,
    )
    raw_answers = dict(body.get('answers') or {})
    for code in ('use_by', 'product_temperature', 'spec_check', 'production_date'):
        if code in body and code not in raw_answers:
            raw_answers[code] = body[code]
    try:
        checked_by = _actor_id(body)
        normalized, items_by_code = _partial_answers(template, raw_answers)
    except (ValueError, QcAnswerError) as exc:
        raise LineQcError(str(exc)) from exc

    claim_lock(dline, checked_by, noun='line')

    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=checked_by,
        scope=GoodsInCheckScope.LINE,
        delivery=delivery,
        delivery_line=dline,
    )
    saved = load_answers(delivery_line=dline)
    dline.line_checks = _merge_checks(dline.line_checks, saved)
    dline.line_template_id = template.id
    dline.line_template_version = template.version
    dline.save()
    line.line_checks = _merge_checks(line.line_checks, saved)
    line.save(update_fields=['line_checks', 'updated_at'])
    return resolve_goods_in_form(po.id, delivery_id=delivery.id)


@transaction.atomic
def draft_adhoc_header_qc(session_id: int, *, body: dict) -> dict:
    session = (
        AdhocGoodsInSession.objects.select_for_update(of=('self',))
        .select_related('product', 'location')
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise AdhocGoodsInError('Adhoc goods-in session not found.')
    if session.status != AdhocGoodsInStatus.OPEN:
        raise AdhocGoodsInError(
            f'Cannot draft header QC when status is {session.status}.',
        )
    if session.checked_at is not None:
        raise AdhocGoodsInError('Header QC already submitted. Use POST to change it.')

    product = session.product
    try:
        technical = product.technical
    except ObjectDoesNotExist:
        technical = None
    tech_by_product = {product.id: technical} if technical else {}

    class _Line:
        def __init__(self, p):
            self.product = p
            self.product_id = p.id

    header_type, header_regime = _header_key([_Line(product)], tech_by_product)
    try:
        template = resolve_template(
            goods_in_type=header_type,
            storage_regime=header_regime,
            scope=GoodsInCheckScope.HEADER,
        )
        checked_by = _actor_id(body)
        normalized, items_by_code = _partial_answers(template, body.get('answers'))
    except (ValueError, QcAnswerError) as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    claim_lock(session, checked_by, noun='delivery')

    if body.get('delivery_date') not in (None, ''):
        try:
            session.delivery_at = parse_date(body.get('delivery_date'), 'delivery_date')
        except QcAnswerError as exc:
            raise AdhocGoodsInError(str(exc)) from exc
        if not session.delivery_trace_number:
            session.delivery_trace_number = julian_trace_number(session.delivery_at)

    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=checked_by,
        scope=GoodsInCheckScope.HEADER,
        adhoc_session=session,
    )
    session.header_checks = _merge_checks(
        session.header_checks,
        load_answers(adhoc_session=session),
    )
    session.header_template_id = template.id
    session.header_template_version = template.version
    session.save()
    return session_form_dict(_load_session(session.id))


@transaction.atomic
def draft_adhoc_line_qc(session_id: int, *, body: dict) -> dict:
    session = (
        AdhocGoodsInSession.objects.select_for_update(of=('self',))
        .select_related('product', 'location', 'line')
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise AdhocGoodsInError('Adhoc goods-in session not found.')
    if session.status in (
        AdhocGoodsInStatus.REJECTED,
        AdhocGoodsInStatus.RECEIVED,
    ):
        raise AdhocGoodsInError(
            f'Cannot draft line QC when status is {session.status}.',
        )
    line = session.line
    if line.line_check_ok:
        raise AdhocGoodsInError('Line QC already submitted. Use POST to change it.')

    product = session.product
    try:
        technical = product.technical
    except ObjectDoesNotExist:
        technical = None
    try:
        template = resolve_template(
            goods_in_type=effective_goods_in_type(product),
            storage_regime=technical.storage_regime if technical else None,
            scope=GoodsInCheckScope.LINE,
        )
    except GoodsInFormError as exc:
        raise AdhocGoodsInError(str(exc)) from exc
    raw_answers = dict(body.get('answers') or {})
    for code in ('use_by', 'product_temperature', 'spec_check', 'production_date'):
        if code in body and code not in raw_answers:
            raw_answers[code] = body[code]
    try:
        checked_by = _actor_id(body)
        normalized, items_by_code = _partial_answers(template, raw_answers)
    except (ValueError, QcAnswerError) as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    claim_lock(line, checked_by, noun='line')

    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=checked_by,
        scope=GoodsInCheckScope.LINE,
        adhoc_session=session,
        adhoc_line=line,
    )
    saved = load_answers(adhoc_line=line)
    line.line_checks = _merge_checks(line.line_checks, saved)
    line.line_template_id = template.id
    line.line_template_version = template.version
    line.save()
    return session_form_dict(_load_session(session.id))
