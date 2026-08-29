"""Without-PO goods-in QC session (Chunk 2). Receive is Chunk 3."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from locations.models import Location
from product.goods_in import effective_goods_in_type
from product.models import Product
from purchasing.models import (
    AdhocGoodsInLine,
    AdhocGoodsInSession,
    AdhocGoodsInStatus,
    GoodsInCheckScope,
)
from purchasing.services.goods_in_form import (
    GoodsInFormError,
    _header_key,
    _template_block,
    resolve_template,
)
from purchasing.services.julian import julian_trace_number
from purchasing.services.line_qc import _shelf_life_ok, _temp_bounds
from purchasing.services.qc_answers import (
    QcAnswerError,
    answer_fails,
    normalize_answer,
    parse_date,
)


class AdhocGoodsInError(ValueError):
    pass


def _iso_date(value):
    return value.isoformat() if value else None


def _load_session(session_id: int) -> AdhocGoodsInSession:
    try:
        return (
            AdhocGoodsInSession.objects.select_related('product', 'location', 'line')
            .get(pk=session_id)
        )
    except AdhocGoodsInSession.DoesNotExist as exc:
        raise AdhocGoodsInError('Adhoc goods-in session not found.') from exc


def session_form_dict(session: AdhocGoodsInSession) -> dict:
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
        header_template = resolve_template(
            goods_in_type=header_type,
            storage_regime=header_regime,
            scope=GoodsInCheckScope.HEADER,
        )
        line_template = resolve_template(
            goods_in_type=effective_goods_in_type(product),
            storage_regime=technical.storage_regime if technical else None,
            scope=GoodsInCheckScope.LINE,
        )
    except GoodsInFormError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    line = session.line
    suggested_delivery = session.delivery_at or date.today()
    suggested_trace = (
        session.delivery_trace_number
        or julian_trace_number(suggested_delivery)
    )
    return {
        'session_id': session.id,
        'status': session.status,
        'location_id': session.location_id,
        'location_name': session.location.name if session.location_id else None,
        'product_id': product.id,
        'product_name': product.name,
        'goods_in_type': effective_goods_in_type(product),
        'storage_regime': technical.storage_regime if technical else None,
        'delivery_at': _iso_date(session.delivery_at),
        'delivery_trace_number': session.delivery_trace_number,
        'suggested_delivery_date': _iso_date(suggested_delivery),
        'suggested_trace_number': suggested_trace,
        'vehicle_temperature': (
            str(session.vehicle_temperature)
            if session.vehicle_temperature is not None
            else None
        ),
        'reject_delivery': session.reject_delivery,
        'checked_at': (
            session.checked_at.isoformat() if session.checked_at else None
        ),
        'checked_by_user_id': session.checked_by_user_id,
        'saved_header_answers': session.header_checks or {},
        'header': _template_block(header_template),
        'line': {
            'line_id': line.id,
            'product_id': product.id,
            'product_name': product.name,
            'saved_answers': line.line_checks or {},
            'line_check_ok': line.line_check_ok,
            'use_by': _iso_date(line.use_by),
            'production_date': _iso_date(line.production_date),
            'trace_number': line.trace_number,
            'product_temperature': (
                str(line.product_temperature)
                if line.product_temperature is not None
                else None
            ),
            'template': _template_block(line_template),
        },
    }


@transaction.atomic
def start_adhoc_goods_in(
    *,
    product_id: int,
    location_id: int,
    created_by_user_id: int | None = None,
) -> dict:
    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist as exc:
        raise AdhocGoodsInError('Product not found.') from exc
    try:
        location = Location.objects.get(pk=location_id)
    except Location.DoesNotExist as exc:
        raise AdhocGoodsInError('Location not found.') from exc

    session = AdhocGoodsInSession.objects.create(
        product=product,
        location=location,
        created_by_user_id=created_by_user_id,
    )
    AdhocGoodsInLine.objects.create(session=session, product=product)
    session = _load_session(session.id)
    return session_form_dict(session)


@transaction.atomic
def submit_adhoc_header_qc(session_id: int, *, body: dict) -> dict:
    session = (
        AdhocGoodsInSession.objects.select_for_update(of=('self',))
        .select_related('product', 'location')
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise AdhocGoodsInError('Adhoc goods-in session not found.')
    if session.status == AdhocGoodsInStatus.REJECTED:
        raise AdhocGoodsInError('Session is rejected; start a new one.')
    if session.status == AdhocGoodsInStatus.QC_COMPLETE:
        raise AdhocGoodsInError('Session QC is already complete.')

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
    except GoodsInFormError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    raw_answers = body.get('answers') or {}
    if not isinstance(raw_answers, dict):
        raise AdhocGoodsInError('answers must be an object keyed by check code.')

    normalized = {}
    failed_codes = []
    for item in template.items.all():
        if item.code not in raw_answers:
            if item.required:
                raise AdhocGoodsInError(f'Answer required for {item.code}.')
            continue
        try:
            answer = normalize_answer(item, raw_answers[item.code])
        except QcAnswerError as exc:
            raise AdhocGoodsInError(str(exc)) from exc
        normalized[item.code] = answer
        if item.is_critical and answer_fails(item, answer):
            failed_codes.append(item.code)

    try:
        delivery_date = parse_date(
            body.get('delivery_date') or session.delivery_at or date.today(),
            'delivery_date',
        )
    except QcAnswerError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    trace_override = body.get('trace_override')
    override_reason = (body.get('override_reason') or '').strip() or None
    if trace_override not in (None, ''):
        if not override_reason:
            raise AdhocGoodsInError(
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

    checked_by = body.get('checked_by_user_id')
    if checked_by in (None, ''):
        raise AdhocGoodsInError('checked_by_user_id is required.')
    try:
        checked_by = int(checked_by)
    except (TypeError, ValueError) as exc:
        raise AdhocGoodsInError('checked_by_user_id must be an integer.') from exc

    now = timezone.now()
    session.delivery_at = delivery_date
    session.delivery_trace_number = trace_number
    session.vehicle_temperature = vehicle_temp
    session.reject_delivery = reject
    session.header_checks = normalized
    session.header_template_id = template.id
    session.header_template_version = template.version
    session.checked_by_user_id = checked_by
    session.checked_at = now
    session.status = (
        AdhocGoodsInStatus.REJECTED if reject else AdhocGoodsInStatus.OPEN
    )

    qc_tl = body.get('qc_tl_checked_by_user_id')
    if qc_tl not in (None, ''):
        session.qc_tl_checked_by_user_id = int(qc_tl)
        session.qc_tl_checked_at = now
        session.qc_tl_comment = body.get('qc_tl_comment') or None
    elif body.get('qc_tl_comment'):
        session.qc_tl_comment = body.get('qc_tl_comment')

    session.save()
    return session_form_dict(_load_session(session.id))


@transaction.atomic
def submit_adhoc_line_qc(session_id: int, *, body: dict) -> dict:
    session = (
        AdhocGoodsInSession.objects.select_for_update(of=('self',))
        .select_related('product', 'location', 'line')
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise AdhocGoodsInError('Adhoc goods-in session not found.')
    if session.status == AdhocGoodsInStatus.REJECTED:
        raise AdhocGoodsInError('Cannot run line QC on a rejected session.')
    if session.checked_at is None:
        raise AdhocGoodsInError('Complete header QC before line QC.')

    line = session.line
    product = session.product
    try:
        technical = product.technical
    except ObjectDoesNotExist:
        technical = None
    storage_regime = technical.storage_regime if technical else None

    try:
        template = resolve_template(
            goods_in_type=effective_goods_in_type(product),
            storage_regime=storage_regime,
            scope=GoodsInCheckScope.LINE,
        )
    except GoodsInFormError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    raw_answers = body.get('answers') or {}
    if not isinstance(raw_answers, dict):
        raise AdhocGoodsInError('answers must be an object keyed by check code.')

    for code in ('use_by', 'product_temperature', 'spec_check', 'production_date'):
        if code in body and code not in raw_answers:
            raw_answers[code] = body[code]

    temp_lo, temp_hi = _temp_bounds(technical, storage_regime)
    normalized = {}
    failed_codes = []
    warnings = []

    for item in template.items.all():
        if item.code not in raw_answers:
            if item.required:
                raise AdhocGoodsInError(f'Answer required for {item.code}.')
            continue
        try:
            answer = normalize_answer(item, raw_answers[item.code])
        except QcAnswerError as exc:
            raise AdhocGoodsInError(str(exc)) from exc
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

    delivery_date = session.delivery_at or date.today()

    use_by = None
    if 'use_by' in normalized and normalized['use_by'].get('value'):
        try:
            use_by = parse_date(normalized['use_by']['value'], 'use_by')
        except QcAnswerError as exc:
            raise AdhocGoodsInError(str(exc)) from exc

    if use_by is not None:
        try:
            acceptance = product.acceptance
            min_days = acceptance.min_acceptable_shelf_life_days
        except ObjectDoesNotExist:
            min_days = None
        if not _shelf_life_ok(
            use_by=use_by,
            delivery_date=delivery_date,
            min_days=min_days,
        ):
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
            raise AdhocGoodsInError(str(exc)) from exc
    elif body.get('production_date') not in (None, ''):
        try:
            production_date = parse_date(
                body.get('production_date'), 'production_date',
            )
        except QcAnswerError as exc:
            raise AdhocGoodsInError(str(exc)) from exc

    trace_override = body.get('trace_override')
    if trace_override not in (None, ''):
        trace_number = str(trace_override).strip()
    else:
        trace_number = (
            body.get('trace_number')
            or session.delivery_trace_number
            or line.trace_number
        )

    line_ok = len(failed_codes) == 0
    line.line_checks = normalized
    line.line_template_id = template.id
    line.line_template_version = template.version
    line.line_check_ok = line_ok
    line.use_by = use_by
    line.product_temperature = product_temp
    line.production_date = production_date
    line.trace_number = trace_number
    line.save()

    if line_ok:
        session.status = AdhocGoodsInStatus.QC_COMPLETE
        session.save(update_fields=['status', 'updated_at'])

    return session_form_dict(_load_session(session.id))


def get_adhoc_goods_in(session_id: int) -> dict:
    return session_form_dict(_load_session(session_id))
