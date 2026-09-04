from datetime import date
from decimal import Decimal, InvalidOperation

from product.goods_in import effective_goods_in_type, product_is_direct_consume
from product.models import ProductGoodsInType, ProductStorageRegime, ProductTechnical
from purchasing.models import (
    GoodsInCheckScope,
    GoodsInCheckTemplate,
    PurchaseOrder,
    PurchaseOrderDeliveryLine,
)
from purchasing.services.delivery import (
    DeliveryError,
    get_delivery,
    latest_delivery_for,
    open_delivery_for,
)
from purchasing.services.julian import julian_trace_number
from purchasing.services.qc_answer_store import load_answers
from purchasing.services.qc_lock import lock_info
from purchasing.services.attachments import list_attachments
from purchasing.services.po import get_purchase_order
from purchasing.serialize import _qty_str, rbac_names, shortfall_reason_options
from purchasing.services.po_qty import queued_hold_by_line_no
from stock_ledger.models import (
    StockEntry,
    StockEntryLabel,
    StockEntryLabelStatus,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util import entry_labels


class GoodsInFormError(ValueError):
    pass


def _iso_date(value):
    return value.isoformat() if value else None


def _item_dict(item) -> dict:
    return {
        'code': item.code,
        'label': item.label,
        'input_type': item.input_type,
        'required': item.required,
        'is_critical': item.is_critical,
        'fail_when': item.fail_when,
        'min_value': str(item.min_value) if item.min_value is not None else None,
        'max_value': str(item.max_value) if item.max_value is not None else None,
        'source': item.source,
        'allows_comment': item.allows_comment,
        'sort_order': item.sort_order,
    }


def _document_dict(template: GoodsInCheckTemplate) -> dict:
    return {
        'document_no': template.document_no,
        'issue_no': template.issue_no,
        'issue_date': _iso_date(template.issue_date),
        'review_date': _iso_date(template.review_date),
        'previous_issue_date': _iso_date(template.previous_issue_date),
        'reason_for_change': template.reason_for_change,
    }


def _template_block(template: GoodsInCheckTemplate) -> dict:
    return {
        'template_id': template.id,
        'template_name': template.name,
        'goods_in_type': template.goods_in_type,
        'storage_regime': template.storage_regime,
        'scope': template.scope,
        'version': template.version,
        'document': _document_dict(template),
        'items': [_item_dict(item) for item in template.items.all()],
    }


def resolve_template(
    *,
    goods_in_type: str | None,
    storage_regime: str | None,
    scope: str,
) -> GoodsInCheckTemplate:
    gin_type = goods_in_type or ProductGoodsInType.OTHER
    qs = (
        GoodsInCheckTemplate.objects.filter(is_active=True, scope=scope)
        .prefetch_related('items')
        .order_by('-version')
    )

    if storage_regime:
        hit = qs.filter(
            goods_in_type=gin_type,
            storage_regime=storage_regime,
        ).first()
        if hit is not None:
            return hit

    hit = qs.filter(goods_in_type=gin_type, storage_regime__isnull=True).first()
    if hit is not None:
        return hit

    hit = qs.filter(
        goods_in_type=ProductGoodsInType.OTHER,
        storage_regime__isnull=True,
    ).first()
    if hit is not None:
        return hit

    raise GoodsInFormError(
        f'No active goods-in template for type={gin_type} '
        f'regime={storage_regime or "*"} scope={scope}. '
        f'Run: python manage.py seed_goods_in_templates',
    )


_REGIME_RANK = {
    ProductStorageRegime.FROZEN: 3,
    ProductStorageRegime.CHILLED: 2,
    ProductStorageRegime.AMBIENT: 1,
}


def _strictest_regime(regimes: list[str | None]) -> str | None:
    best = None
    best_rank = -1
    for regime in regimes:
        rank = _REGIME_RANK.get(regime, 0)
        if rank > best_rank:
            best_rank = rank
            best = regime
    return best


def _header_key(lines, tech_by_product: dict) -> tuple[str, str | None]:
    if not lines:
        return ProductGoodsInType.OTHER, None

    for line in lines:
        gin_type = effective_goods_in_type(line.product)
        if gin_type == ProductGoodsInType.PACKAGING:
            tech = tech_by_product.get(line.product_id)
            return gin_type, tech.storage_regime if tech else None

    rm_regimes = []
    for line in lines:
        if effective_goods_in_type(line.product) != ProductGoodsInType.RAW_MATERIAL:
            continue
        tech = tech_by_product.get(line.product_id)
        rm_regimes.append(tech.storage_regime if tech else None)
    if rm_regimes:
        return ProductGoodsInType.RAW_MATERIAL, _strictest_regime(rm_regimes)

    first = lines[0]
    gin_type = effective_goods_in_type(first.product)
    tech = tech_by_product.get(first.product_id)
    return gin_type, tech.storage_regime if tech else None


_LINE_STEP_ORDER = (
    'line_qc',
    'received',
    'print_label',
    'verify_label',
    'posted',
)


def _positive(value) -> bool:
    try:
        return Decimal(str(value or 0)) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _label_printed(label: StockEntryLabel | None) -> bool:
    if label is None:
        return False
    if label.printed_at is not None:
        return True
    return label.status in (
        StockEntryLabelStatus.PRINTED,
        StockEntryLabelStatus.VERIFIED,
    )


def _entry_label_step(entry: StockEntry) -> dict:
    try:
        label = entry.label
    except StockEntryLabel.DoesNotExist:
        label = None
    try:
        posting = entry.posting
    except StockEntryPosting.DoesNotExist:
        posting = None
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'print_label': _label_printed(label),
        'verify_label': bool(
            label is not None
            and label.status == StockEntryLabelStatus.VERIFIED
        ),
        'posted': (
            posting is None
            or posting.status == StockEntryPostingStatus.POSTED
        ),
    }


def _receipt_entries(po_ids: list[int]):
    if not po_ids:
        return StockEntry.objects.none()
    return (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.RECEIPT,
            source_document_type='po',
            source_document_id__in=po_ids,
        )
        .exclude(posting__status=StockEntryPostingStatus.CANCELLED)
        .select_related('label', 'posting')
        .order_by('id')
    )


def _entry_delivery_id(entry: StockEntry) -> int | None:
    """Delivery stamped on the posting at receive; None for older rows."""
    try:
        posting = entry.posting
    except StockEntryPosting.DoesNotExist:
        return None
    raw = (posting.meta or {}).get('delivery_id')
    return int(raw) if raw not in (None, '') else None


def _label_steps_by_po_ids(po_ids: list[int]) -> dict[int, dict[int, list[dict]]]:
    by_po: dict[int, dict[int, list[dict]]] = {}
    for entry in _receipt_entries(po_ids):
        line_no = entry.source_document_line
        if line_no is None or entry.source_document_id is None:
            continue
        by_po.setdefault(entry.source_document_id, {}).setdefault(
            line_no, [],
        ).append(_entry_label_step(entry))
    return by_po


def _label_steps_by_line_no(po_id: int) -> dict[int, list[dict]]:
    return _label_steps_by_po_ids([po_id]).get(po_id, {})


def _delivery_entry_bounds(po_id: int, delivery_id: int) -> dict[int, tuple[int, int]]:
    """Entry-id window per line_no, for receipts with no delivery stamp.

    Entry ids only grow as receives land, so a visit owns the ids above the
    previous visit's last receipt up to its own.
    """
    rows = (
        PurchaseOrderDeliveryLine.objects
        .filter(delivery__purchase_order_id=po_id)
        .order_by('delivery_id')
        .values_list('delivery_id', 'po_line__line_no', 'last_receipt_entry_id')
    )
    bounds: dict[int, tuple[int, int]] = {}
    floor: dict[int, int] = {}
    for row_delivery_id, line_no, last_entry_id in rows:
        if row_delivery_id == delivery_id:
            if last_entry_id is not None:
                bounds[line_no] = (floor.get(line_no, 0), last_entry_id)
        elif row_delivery_id < delivery_id and last_entry_id is not None:
            floor[line_no] = max(floor.get(line_no, 0), last_entry_id)
    return bounds


def _label_steps_for_delivery(po_id: int, delivery_id: int | None) -> dict[int, list[dict]]:
    """This visit's labels only: entries stamped with another delivery drop out."""
    if delivery_id is None:
        return _label_steps_by_line_no(po_id)
    bounds = _delivery_entry_bounds(po_id, delivery_id)
    by_line: dict[int, list[dict]] = {}
    for entry in _receipt_entries([po_id]):
        line_no = entry.source_document_line
        if line_no is None:
            continue
        stamped = _entry_delivery_id(entry)
        if stamped is None:
            lower, upper = bounds.get(line_no, (0, 0))
            if not lower < entry.id <= upper:
                continue
        elif stamped != delivery_id:
            continue
        by_line.setdefault(line_no, []).append(_entry_label_step(entry))
    return by_line


def _thin_steps(po: PurchaseOrder, labels_by_line: dict[int, list[dict]]) -> dict:
    labels = [row for rows in labels_by_line.values() for row in rows]
    queued = [row for row in labels if not row['posted']]
    return {
        'header_qc': po.checked_at is not None,
        'print_label': _all_true(labels, 'print_label'),
        'verify_label': _all_true(labels, 'verify_label'),
        'queued_label_count': len(queued),
    }


def po_list_steps_map(pos: list[PurchaseOrder]) -> dict[int, dict]:
    by_po = _label_steps_by_po_ids([po.id for po in pos])
    return {
        po.id: _thin_steps(po, by_po.get(po.id, {}))
        for po in pos
    }


def _all_true(labels: list[dict], flag: str) -> bool:
    """PO level: no receipts yet means nothing is waiting on a label."""
    return all(row[flag] for row in labels)


def _labels_done(labels: list[dict], flag: str) -> bool:
    """Line level: nothing printed, verified or posted until a label exists."""
    return bool(labels) and all(row[flag] for row in labels)


def _answered(saved: dict, code: str) -> bool:
    raw = (saved or {}).get(code)
    if isinstance(raw, dict):
        return raw.get('value') not in (None, '')
    return raw not in (None, '')


def _check_flags(items, saved: dict) -> dict:
    flags = {}
    for item in items:
        code = item['code'] if isinstance(item, dict) else item.code
        if not code:
            continue
        flags[code] = _answered(saved, code)
    return flags


def _line_steps(block: dict, labels: list[dict]) -> dict:
    per_visit = block.get('delivery_qty_received')
    received = (
        _positive(per_visit) if per_visit is not None
        else _positive(block['qty_received']) or _positive(block['qty_queued'])
    )
    return {
        'line_id': block['line_id'],
        'line_qc': bool(block['line_check_ok']),
        'checks': _check_flags(
            (block.get('template') or {}).get('items') or [],
            block.get('saved_answers') or {},
        ),
        'received': received,
        'print_label': _labels_done(labels, 'print_label'),
        'verify_label': _labels_done(labels, 'verify_label'),
        'posted': _labels_done(labels, 'posted'),
        'labels': labels,
    }


def _current_step(header_qc: bool, line_steps: list[dict]) -> str:
    if not header_qc:
        return 'header_qc'
    for row in line_steps:
        for flag in _LINE_STEP_ORDER:
            if not row[flag]:
                return flag
    return 'done'


def _answers_block(saved_header: dict, line_blocks: list[dict]) -> dict:
    lines = {}
    for block in line_blocks:
        lines[str(block['line_id'])] = {
            **(block.get('saved_answers') or {}),
            'qty_received': block['qty_received'],
            'qty_queued': block['qty_queued'],
            'qty_balance': block['qty_balance'],
        }
    return {'header': saved_header or {}, 'lines': lines}


def resolve_goods_in_form(po_id: int, delivery_id: int | None = None) -> dict:
    try:
        po = get_purchase_order(po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise GoodsInFormError('Purchase order not found.') from exc

    delivery = None
    if delivery_id is not None:
        try:
            delivery = get_delivery(po.id, delivery_id)
        except DeliveryError as exc:
            raise GoodsInFormError(str(exc)) from exc
    else:
        delivery = open_delivery_for(po.id) or latest_delivery_for(po.id)

    session = delivery if delivery is not None else po
    dlines = {}
    if delivery is not None:
        dlines = {
            row.po_line_id: row
            for row in delivery.lines.all()
        }

    lines = list(po.lines.all())
    tech_by_product = {
        row.product_id: row
        for row in ProductTechnical.objects.filter(
            product_id__in=[line.product_id for line in lines],
        )
    }

    header_type, header_regime = _header_key(lines, tech_by_product)
    header_template = resolve_template(
        goods_in_type=header_type,
        storage_regime=header_regime,
        scope=GoodsInCheckScope.HEADER,
    )

    line_blocks = []
    holds = queued_hold_by_line_no(po.id)
    for line in lines:
        gin_type = effective_goods_in_type(line.product)
        tech = tech_by_product.get(line.product_id)
        regime = tech.storage_regime if tech is not None else None
        line_template = resolve_template(
            goods_in_type=gin_type,
            storage_regime=regime,
            scope=GoodsInCheckScope.LINE,
        )
        dline = dlines.get(line.id)
        if dline is not None:
            saved_answers = load_answers(delivery_line=dline) or dline.line_checks or {}
            line_check_ok = dline.line_check_ok
        elif delivery is None:
            saved_answers = line.line_checks or {}
            line_check_ok = line.line_check_ok
        else:
            saved_answers = {}
            line_check_ok = False
        line_blocks.append({
            'line_id': line.id,
            'line_no': line.line_no,
            'product_id': line.product_id,
            'product_name': line.product.name,
            'goods_in_type': gin_type,
            'direct_consume': product_is_direct_consume(line.product),
            'storage_regime': regime,
            'qty_ordered': _qty_str(line.qty_ordered),
            'qty_received': _qty_str(line.qty_received),
            'delivery_qty_received': (
                None if delivery is None
                else _qty_str(dline.qty_received if dline is not None else Decimal('0'))
            ),
            'qty_rejected': _qty_str(line.qty_rejected),
            'qty_queued': _qty_str(holds.get(line.line_no, 0)),
            'qty_balance': _qty_str(line.qty_balance),
            'use_by': _iso_date(
                dline.use_by if dline is not None and dline.use_by else line.use_by,
            ),
            'line_closed': line.line_closed,
            'shortfall_reason': line.shortfall_reason,
            'needs_credit_note': line.qty_rejected > 0,
            'pack_size': line.shape_format_label,
            'unit_id': line.unit_id,
            'unit_name': line.unit.name if line.unit_id else None,
            'saved_answers': saved_answers,
            'line_check_ok': line_check_ok,
            'qc_draft': bool(saved_answers) and not line_check_ok,
            'lock': lock_info(dline) if dline is not None else None,
            'label_format': line.label_format,
            'label_count': line.label_count,
            'template': _template_block(line_template),
        })

    suggested_delivery_date = (
        session.delivery_at or po.expected_at or date.today()
    )
    suggested_trace = (
        session.delivery_trace_number
        or julian_trace_number(suggested_delivery_date)
    )
    names = rbac_names({session.checked_by_user_id, session.qc_tl_checked_by_user_id})
    saved_header = (
        (load_answers(delivery=delivery) if delivery is not None else {})
        or session.header_checks
        or {}
    )
    header_qc = session.checked_at is not None
    labels_by_line = _label_steps_for_delivery(
        po.id, delivery.id if delivery is not None else None,
    )
    line_steps = [
        _line_steps(block, labels_by_line.get(block['line_no'], []))
        for block in line_blocks
    ]
    if delivery_id is None:
        resume = delivery
    else:
        resume = open_delivery_for(po.id) or latest_delivery_for(po.id) or delivery

    return {
        'purchase_order_id': po.id,
        'delivery_id': delivery.id if delivery is not None else None,
        'resume_delivery_id': resume.id if resume is not None else None,
        'delivery_status': delivery.status if delivery is not None else None,
        'number': po.external_number or po.number,
        'sage_po_number': po.external_number,
        'system_number': po.number,
        'status': po.status,
        'supplier_id': po.supplier_id,
        'supplier_name': po.supplier.name if po.supplier_id else None,
        'ship_to_location_id': po.ship_to_location_id,
        'expected_at': _iso_date(po.expected_at),
        'ordered_at': _iso_date(po.ordered_at),
        'delivery_at': _iso_date(session.delivery_at),
        'suggested_delivery_date': _iso_date(suggested_delivery_date),
        'delivery_trace_number': session.delivery_trace_number,
        'suggested_trace_number': suggested_trace,
        'reject_delivery': session.reject_delivery,
        'vehicle_temperature': (
            str(session.vehicle_temperature)
            if session.vehicle_temperature is not None else None
        ),
        'checked_by_user_id': session.checked_by_user_id,
        'checked_by_name': names.get(session.checked_by_user_id),
        'checked_at': session.checked_at.isoformat() if session.checked_at else None,
        'qc_tl_checked_by_user_id': session.qc_tl_checked_by_user_id,
        'qc_tl_checked_by_name': names.get(session.qc_tl_checked_by_user_id),
        'qc_tl_checked_at': (
            session.qc_tl_checked_at.isoformat() if session.qc_tl_checked_at else None
        ),
        'qc_tl_comment': session.qc_tl_comment,
        'saved_header_answers': saved_header,
        'qc_draft': bool(saved_header) and session.checked_at is None,
        'lock': lock_info(delivery) if delivery is not None else None,
        'header': _template_block(header_template),
        'shortfall_reasons': shortfall_reason_options(),
        'lines': line_blocks,
        'attachments': list_attachments(
            po.id,
            delivery_id=delivery.id if delivery is not None else None,
        ),
        'steps': {
            'current': _current_step(header_qc, line_steps),
            'header_qc': header_qc,
            'header': _check_flags(header_template.items.all(), saved_header),
            'lines': line_steps,
        },
        'answers': _answers_block(saved_header, line_blocks),
    }
