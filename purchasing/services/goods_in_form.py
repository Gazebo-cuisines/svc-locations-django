from datetime import date

from product.goods_in import effective_goods_in_type
from product.models import ProductGoodsInType, ProductTechnical
from purchasing.models import (
    GoodsInCheckScope,
    GoodsInCheckTemplate,
    PurchaseOrder,
)
from purchasing.services.julian import julian_trace_number
from purchasing.services.attachments import list_attachments
from purchasing.services.po import get_purchase_order
from purchasing.serialize import _qty_str, rbac_names, shortfall_reason_options


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


def _header_key(lines, tech_by_product: dict) -> tuple[str, str | None]:
    if not lines:
        return ProductGoodsInType.OTHER, None

    for line in lines:
        gin_type = effective_goods_in_type(line.product)
        if gin_type == ProductGoodsInType.PACKAGING:
            tech = tech_by_product.get(line.product_id)
            return gin_type, tech.storage_regime if tech else None

    first = lines[0]
    gin_type = effective_goods_in_type(first.product)
    tech = tech_by_product.get(first.product_id)
    return gin_type, tech.storage_regime if tech else None


def resolve_goods_in_form(po_id: int) -> dict:
    try:
        po = get_purchase_order(po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise GoodsInFormError('Purchase order not found.') from exc

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
    for line in lines:
        gin_type = effective_goods_in_type(line.product)
        tech = tech_by_product.get(line.product_id)
        regime = tech.storage_regime if tech is not None else None
        line_template = resolve_template(
            goods_in_type=gin_type,
            storage_regime=regime,
            scope=GoodsInCheckScope.LINE,
        )
        line_blocks.append({
            'line_id': line.id,
            'line_no': line.line_no,
            'product_id': line.product_id,
            'product_name': line.product.name,
            'goods_in_type': gin_type,
            'storage_regime': regime,
            'qty_ordered': _qty_str(line.qty_ordered),
            'qty_received': _qty_str(line.qty_received),
            'qty_rejected': _qty_str(line.qty_rejected),
            'qty_balance': _qty_str(line.qty_balance),
            'shortfall_reason': line.shortfall_reason,
            'needs_credit_note': line.qty_rejected > 0,
            'pack_size': line.shape_format_label,
            'unit_id': line.unit_id,
            'unit_name': line.unit.name if line.unit_id else None,
            'saved_answers': line.line_checks or {},
            'line_check_ok': line.line_check_ok,
            'label_format': line.label_format,
            'label_count': line.label_count,
            'template': _template_block(line_template),
        })

    suggested_delivery_date = (
        po.delivery_at or po.expected_at or date.today()
    )
    suggested_trace = (
        po.delivery_trace_number
        or julian_trace_number(suggested_delivery_date)
    )
    names = rbac_names({po.checked_by_user_id, po.qc_tl_checked_by_user_id})

    return {
        'purchase_order_id': po.id,
        'number': po.external_number or po.number,
        'sage_po_number': po.external_number,
        'system_number': po.number,
        'status': po.status,
        'supplier_id': po.supplier_id,
        'supplier_name': po.supplier.name if po.supplier_id else None,
        'ship_to_location_id': po.ship_to_location_id,
        'expected_at': _iso_date(po.expected_at),
        'ordered_at': _iso_date(po.ordered_at),
        'delivery_at': _iso_date(po.delivery_at),
        'suggested_delivery_date': _iso_date(suggested_delivery_date),
        'delivery_trace_number': po.delivery_trace_number,
        'suggested_trace_number': suggested_trace,
        'reject_delivery': po.reject_delivery,
        'vehicle_temperature': (
            str(po.vehicle_temperature)
            if po.vehicle_temperature is not None else None
        ),
        'checked_by_user_id': po.checked_by_user_id,
        'checked_by_name': names.get(po.checked_by_user_id),
        'checked_at': po.checked_at.isoformat() if po.checked_at else None,
        'qc_tl_checked_by_user_id': po.qc_tl_checked_by_user_id,
        'qc_tl_checked_by_name': names.get(po.qc_tl_checked_by_user_id),
        'qc_tl_checked_at': (
            po.qc_tl_checked_at.isoformat() if po.qc_tl_checked_at else None
        ),
        'qc_tl_comment': po.qc_tl_comment,
        'saved_header_answers': po.header_checks or {},
        'header': _template_block(header_template),
        'shortfall_reasons': shortfall_reason_options(),
        'lines': line_blocks,
        'attachments': list_attachments(po.id),
    }
