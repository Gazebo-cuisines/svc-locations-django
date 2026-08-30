"""Without-PO goods-in QC + receive (Chunks 2–3). Never touches PurchaseOrder."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from locations.models import Location
from product.goods_in import effective_goods_in_type
from product.models import Product, ProductSupplier, Unit
from purchasing.models import (
    AdhocGoodsInLine,
    AdhocGoodsInSession,
    AdhocGoodsInStatus,
    GoodsInCheckScope,
)
from purchasing.serialize import _qty_str
from purchasing.services.goods_in_form import (
    GoodsInFormError,
    _header_key,
    _template_block,
    resolve_template,
)
from purchasing.services.julian import julian_trace_number
from purchasing.services.line_qc import _shelf_life_ok, _temp_bounds
from purchasing.services.qc_answer_store import load_answers, upsert_answers
from purchasing.services.qc_lock import claim_lock, lock_info
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
from stock_ledger.models import StockEntry, StockLotOrigin
from stock_ledger.util import entry_labels
from stock_ledger.util import entry_posting
from stock_ledger.util import services as stock_services
from stock_ledger.util import stock_units
from stock_ledger.util.conversions import (
    StockValidationError,
    packs_to_stock,
    to_product_unit,
)


def _entry_dict(entry: StockEntry) -> dict:
    # Lazy: views import purchasing in places; avoid cycle at module load.
    from stock_ledger.views import entry_dict

    entry = (
        StockEntry.objects
        .select_related(
            'counterparty_location',
            'location',
            'unit',
            'lot__product',
            'lot__shape_format',
        )
        .get(pk=entry.pk)
    )
    return entry_dict(entry)


def _stock_unit_dicts(units) -> list:
    from stock_ledger.views import stock_unit_dict

    return [stock_unit_dict(u) for u in units]


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
    saved_header = load_answers(adhoc_session=session) or session.header_checks or {}
    saved_line = load_answers(adhoc_line=line) or line.line_checks or {}
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
        'item_po_ref': session.item_po_ref,
        'label_format': session.label_format,
        'label_count': session.label_count,
        'saved_header_answers': saved_header,
        'qc_draft': bool(saved_header) and session.checked_at is None,
        'lock': lock_info(session),
        'header': _template_block(header_template),
        'line': {
            'line_id': line.id,
            'product_id': product.id,
            'product_name': product.name,
            'saved_answers': saved_line,
            'line_check_ok': line.line_check_ok,
            'qc_draft': bool(saved_line) and not line.line_check_ok,
            'lock': lock_info(line),
            'use_by': _iso_date(line.use_by),
            'production_date': _iso_date(line.production_date),
            'trace_number': line.trace_number,
            'product_temperature': (
                str(line.product_temperature)
                if line.product_temperature is not None
                else None
            ),
            'product_supplier_id': line.product_supplier_id,
            'qty_entered': (
                str(line.qty_entered) if line.qty_entered is not None else None
            ),
            'stock_qty': (
                str(line.stock_qty) if line.stock_qty is not None else None
            ),
            'shape_format_label': line.shape_format_label,
            'shape_other': line.shape_other,
            'last_receipt_entry_id': line.last_receipt_entry_id,
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
    if session.status in (
        AdhocGoodsInStatus.QC_COMPLETE,
        AdhocGoodsInStatus.RECEIVED,
    ):
        raise AdhocGoodsInError(
            f'Cannot change header QC when status is {session.status}.',
        )

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
    items_by_code = {}
    for item in template.items.all():
        items_by_code[item.code] = item
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

    claim_lock(session, checked_by, noun='delivery')

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
    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=checked_by,
        scope=GoodsInCheckScope.HEADER,
        adhoc_session=session,
    )
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
    if session.status == AdhocGoodsInStatus.RECEIVED:
        raise AdhocGoodsInError('Session already received.')
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
    items_by_code = {}

    for item in template.items.all():
        items_by_code[item.code] = item
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
    claim_lock(line, body.get('checked_by_user_id'), noun='line')
    line.line_checks = normalized
    line.line_template_id = template.id
    line.line_template_version = template.version
    line.line_check_ok = line_ok
    line.use_by = use_by
    line.product_temperature = product_temp
    line.production_date = production_date
    line.trace_number = trace_number
    line.save()
    upsert_answers(
        answers=normalized,
        items_by_code=items_by_code,
        user_id=body.get('checked_by_user_id'),
        scope=GoodsInCheckScope.LINE,
        adhoc_session=session,
        adhoc_line=line,
    )

    if line_ok:
        session.status = AdhocGoodsInStatus.QC_COMPLETE
        session.save(update_fields=['status', 'updated_at'])

    failed_details = build_line_qc_fail_details(
        failed_codes=failed_codes,
        items_by_code=items_by_code,
        normalized=normalized,
        delivery_date=delivery_date,
    )
    form = session_form_dict(_load_session(session.id))
    form['line_check_ok'] = line_ok
    form['failed_codes'] = failed_codes
    form['warnings'] = warnings
    form['failed_details'] = failed_details
    if not line_ok:
        form['qc_blocked_message'] = line_qc_blocked_message()
    form['line']['failed_codes'] = failed_codes
    form['line']['warnings'] = warnings
    form['line']['failed_details'] = failed_details
    return form


def get_adhoc_goods_in(session_id: int) -> dict:
    return session_form_dict(_load_session(session_id))


def _parse_qty(value, field_name: str) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdhocGoodsInError(f'Invalid decimal for {field_name}.') from exc
    if qty <= 0:
        raise AdhocGoodsInError(f'{field_name} must be greater than 0.')
    return qty


def _split_quantities(total: Decimal, parts: int) -> list[Decimal]:
    if parts < 1:
        raise AdhocGoodsInError('label_count must be >= 1.')
    if parts == 1:
        return [total]
    base = (total / parts).quantize(Decimal('0.000001'))
    chunks = [base] * (parts - 1)
    last = (total - sum(chunks)).quantize(Decimal('0.000001'))
    if last <= 0:
        raise AdhocGoodsInError(
            f'Cannot split quantity {total} into {parts} labels.',
        )
    chunks.append(last)
    return chunks


def _label_plan(body: dict) -> tuple[str, int]:
    raw = body.get('label_format')
    if raw in (None, ''):
        raise AdhocGoodsInError('label_format is required (pallet or box).')
    fmt = str(raw).strip().lower()
    if fmt not in ('pallet', 'box'):
        raise AdhocGoodsInError('label_format must be pallet or box.')
    if body.get('label_count') in (None, ''):
        count = 1 if fmt == 'pallet' else 0
    else:
        try:
            count = int(body['label_count'])
        except (TypeError, ValueError) as exc:
            raise AdhocGoodsInError('label_count must be an integer.') from exc
    if count < 1:
        raise AdhocGoodsInError('label_count is required when label_format is set.')
    if fmt == 'pallet' and count != 1:
        raise AdhocGoodsInError('label_format=pallet requires label_count=1.')
    return fmt, count


def _resolve_shape(
    *,
    product: Product,
    body: dict,
) -> tuple[ProductSupplier | None, Decimal, Decimal, str | None, dict | None, int | None]:
    """
    Returns mapping, pack_or_free_qty_for_receipt, stock_qty_preview,
    shape_label, shape_other, unit_id_for_free.
    """
    qty = _parse_qty(body.get('quantity'), 'quantity')
    ps_id = body.get('product_supplier_id')
    shape_other = body.get('shape_other')

    if ps_id not in (None, ''):
        try:
            mapping = ProductSupplier.objects.select_related(
                'outer_unit', 'inner_unit', 'purchase_shape_format',
            ).get(pk=int(ps_id), product_id=product.id, is_active=True)
        except (ProductSupplier.DoesNotExist, TypeError, ValueError) as exc:
            raise AdhocGoodsInError(
                'product_supplier_id not found for this product.',
            ) from exc
        stock = packs_to_stock(qty, mapping, product)
        return mapping, qty, stock, mapping.shape_format_label, None, None

    if isinstance(shape_other, dict) and shape_other:
        try:
            outer_qty = _parse_qty(shape_other.get('outer_qty'), 'shape_other.outer_qty')
            inner_qty = _parse_qty(shape_other.get('inner_qty'), 'shape_other.inner_qty')
            outer_unit_id = int(shape_other['outer_unit_id'])
            inner_unit_id = int(shape_other['inner_unit_id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdhocGoodsInError(
                'shape_other requires outer_qty, outer_unit_id, '
                'inner_qty, inner_unit_id.',
            ) from exc
        if not Unit.objects.filter(pk=outer_unit_id).exists():
            raise AdhocGoodsInError(f'outer_unit_id={outer_unit_id} not found.')
        if not Unit.objects.filter(pk=inner_unit_id).exists():
            raise AdhocGoodsInError(f'inner_unit_id={inner_unit_id} not found.')
        multiplier = ProductSupplier.build_multiplier(outer_qty, inner_qty)
        try:
            stock = to_product_unit(qty * multiplier, inner_unit_id, product)
        except StockValidationError as exc:
            raise AdhocGoodsInError(str(exc)) from exc
        label = shape_other.get('label') or ProductSupplier.build_shape_label(
            outer_qty,
            Unit.objects.get(pk=outer_unit_id).name,
            inner_qty,
            Unit.objects.get(pk=inner_unit_id).name,
            multiplier,
        )
        other = {
            'outer_qty': str(outer_qty),
            'outer_unit_id': outer_unit_id,
            'inner_qty': str(inner_qty),
            'inner_unit_id': inner_unit_id,
            'multiplier': str(multiplier),
            'label': label,
        }
        return None, stock, stock, label, other, product.unit_id

    # Free stock in product unit
    if product.unit_id is None:
        raise AdhocGoodsInError('Product has no stock unit for free-qty receive.')
    return None, qty, qty, None, None, product.unit_id


def _entry_payload(entry: StockEntry) -> dict:
    return _entry_dict(entry)


@transaction.atomic
def receive_adhoc_goods_in(
    session_id: int,
    *,
    body: dict,
    audit: dict | None = None,
) -> dict:
    """Post queued receipt for a QC-complete adhoc session. No PurchaseOrder."""
    audit = dict(audit or {})
    session = (
        AdhocGoodsInSession.objects.select_for_update(of=('self',))
        .select_related('product', 'location', 'line')
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise AdhocGoodsInError('Adhoc goods-in session not found.')
    if session.status == AdhocGoodsInStatus.REJECTED:
        raise AdhocGoodsInError('Cannot receive a rejected session.')
    if session.status not in (
        AdhocGoodsInStatus.QC_COMPLETE,
        AdhocGoodsInStatus.RECEIVED,
    ):
        raise AdhocGoodsInError('Complete line QC before receive.')
    line = session.line
    if not line.line_check_ok:
        raise AdhocGoodsInError('Line QC has not passed.')

    idempotency_key = body.get('idempotency_key')
    if idempotency_key in (None, ''):
        raise AdhocGoodsInError('idempotency_key is required.')
    idempotency_key = str(idempotency_key)

    label_format, label_count = _label_plan(body)
    unit_keys = (
        [idempotency_key]
        if label_count == 1
        else [f'{idempotency_key}:u:{i}' for i in range(1, label_count + 1)]
    )
    prior = StockEntry.objects.filter(idempotency_key=unit_keys[0]).first()
    if prior is not None and session.status == AdhocGoodsInStatus.RECEIVED:
        # Idempotent replay
        form = session_form_dict(_load_session(session.id))
        form['receive_results'] = [{
            'stock_entry_id': prior.id,
            'entry_code': entry_labels.entry_code(prior.id),
            'entry': _entry_payload(prior),
            'idempotent': True,
        }]
        return form

    if session.status == AdhocGoodsInStatus.RECEIVED:
        raise AdhocGoodsInError(
            'Session already received; reuse the same idempotency_key to replay.',
        )

    product = session.product
    try:
        mapping, receipt_qty, stock_qty, shape_label, shape_other, unit_id = (
            _resolve_shape(product=product, body=body)
        )
    except StockValidationError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    item_po_ref = (body.get('item_po_ref') or '').strip() or None
    queue_flag = body.get('queue_stock')
    if queue_flag in (False, 'false', '0', 'no', 'False'):
        queue_stock = False
    else:
        queue_stock = True

    supplier_id = None
    if mapping is not None:
        supplier_id = mapping.supplier_id
    elif body.get('supplier_id') not in (None, ''):
        try:
            supplier_id = int(body['supplier_id'])
        except (TypeError, ValueError) as exc:
            raise AdhocGoodsInError('supplier_id must be an integer.') from exc
        if not Location.objects.filter(pk=supplier_id).exists():
            raise AdhocGoodsInError(f'supplier_id={supplier_id} not found.')

    try:
        lot = stock_services.resolve_lot(
            product_id=product.id,
            trace_number=line.trace_number or session.delivery_trace_number,
            use_by=line.use_by,
            production_date=line.production_date,
            origin=StockLotOrigin.PURCHASE,
            product_supplier_id=mapping.id if mapping else None,
            shape_format_id=(
                mapping.purchase_shape_format_id if mapping else None
            ),
        )
    except StockValidationError as exc:
        raise AdhocGoodsInError(str(exc)) from exc

    remarks_parts = []
    if item_po_ref:
        remarks_parts.append(f'Item PO ref: {item_po_ref}')
    if body.get('remarks'):
        remarks_parts.append(str(body['remarks']))
    receipt_audit = {
        'actor_user_id': (
            audit.get('actor_user_id')
            or body.get('actor_user_id')
            or body.get('checked_by_user_id')
        ),
        'lan_username': audit.get('lan_username') or body.get('lan_username'),
        'source_workstation': audit.get('source_workstation'),
        'source_workstation_ip': audit.get('source_workstation_ip'),
        'remarks': '; '.join(remarks_parts) or None,
        'source_document_type': 'adhoc_goods_in',
        'source_document_id': session.id,
        'source_document_line': 1,
        'counterparty_location_id': supplier_id,
    }
    receipt_audit = {k: v for k, v in receipt_audit.items() if v is not None}

    qty_parts = _split_quantities(receipt_qty, label_count)
    transactions = []
    last_entry = None
    for unit_index, (unit_key, part_qty) in enumerate(
        zip(unit_keys, qty_parts), start=1,
    ):
        try:
            entry = stock_services.receipt(
                idempotency_key=unit_key,
                lot=lot,
                location_id=session.location_id,
                quantity=part_qty,
                unit_id=unit_id,
                product_supplier=mapping,
                defer_balance=queue_stock,
                **receipt_audit,
            )
        except StockValidationError as exc:
            raise AdhocGoodsInError(str(exc)) from exc
        last_entry = entry
        tx = {
            'unit_index': unit_index,
            'stock_entry_id': entry.id,
            'entry_code': entry_labels.entry_code(entry.id),
            'quantity_stock': _qty_str(entry.quantity),
            'lot_id': lot.id,
            'idempotency_key': unit_key,
            'entry': _entry_payload(entry),
        }
        if queue_stock:
            posting = entry_posting.queue_entry(
                entry=entry,
                actor_user_id=receipt_audit.get('actor_user_id'),
                lan_username=receipt_audit.get('lan_username'),
                source_workstation=receipt_audit.get('source_workstation'),
                meta={
                    'adhoc_session_id': session.id,
                    'item_po_ref': item_po_ref,
                },
            )
            tx['posting'] = entry_posting.posting_dict(posting)
            tx['posting_status'] = posting.status
        if label_format:
            label = entry_labels.create_entry_label(
                entry=entry,
                label_format=label_format,
                label_count=1,
                actor_user_id=receipt_audit.get('actor_user_id'),
                lan_username=receipt_audit.get('lan_username'),
                source_workstation=receipt_audit.get('source_workstation'),
            )
            tx['label'] = entry_labels.label_state_dict(label)
            tx['goods_in_label'] = entry_labels.build_goods_in_label(entry, label)
        if unit_index == 1:
            print_count = body.get('print_unit_count')
            print_qty = body.get('print_quantity_per_unit')
            if print_count not in (None, '') or print_qty not in (None, ''):
                if print_count in (None, '') or print_qty in (None, ''):
                    raise AdhocGoodsInError(
                        'print_unit_count and print_quantity_per_unit '
                        'are both required to print labels.',
                    )
                prefix = body.get('print_idempotency_key_prefix') or f'{unit_key}:print'
                units = stock_units.create_units_for_entry(
                    source_entry=entry,
                    unit_count=int(print_count),
                    quantity_per_unit=_parse_qty(
                        print_qty, 'print_quantity_per_unit',
                    ),
                    idempotency_key_prefix=str(prefix),
                    actor_user_id=receipt_audit.get('actor_user_id'),
                    lan_username=receipt_audit.get('lan_username'),
                    source_workstation=receipt_audit.get('source_workstation'),
                )
                tx['units'] = _stock_unit_dicts(units)
        transactions.append(tx)

    line.product_supplier = mapping
    line.qty_entered = _parse_qty(body.get('quantity'), 'quantity')
    line.stock_qty = stock_qty
    line.shape_format_label = shape_label
    line.shape_other = shape_other
    line.last_receipt_entry_id = last_entry.id if last_entry else None
    line.save()

    session.item_po_ref = item_po_ref
    session.label_format = label_format
    session.label_count = label_count
    session.receive_idempotency_key = idempotency_key
    session.status = AdhocGoodsInStatus.RECEIVED
    session.save()

    form = session_form_dict(_load_session(session.id))
    form['receive_results'] = transactions
    form['label_format'] = label_format
    form['label_count'] = label_count
    form['stock_qty'] = _qty_str(stock_qty)
    return form
