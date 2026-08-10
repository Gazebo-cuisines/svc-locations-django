from django.core.exceptions import ObjectDoesNotExist

from product.models import ProductGoodsInType
from purchasing.models import (
    GoodsInCheckScope,
    GoodsInCheckTemplate,
    PurchaseOrder,
)
from purchasing.services.po import get_purchase_order


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
        GoodsInCheckTemplate.objects.filter(
            is_active=True,
            scope=scope,
        )
        .prefetch_related('items')
        .order_by('-version')
    )

    # Exact type + regime
    if storage_regime:
        hit = qs.filter(
            goods_in_type=gin_type,
            storage_regime=storage_regime,
        ).first()
        if hit is not None:
            return hit

    # Type fallback (regime null)
    hit = qs.filter(goods_in_type=gin_type, storage_regime__isnull=True).first()
    if hit is not None:
        return hit

    # Global other
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


def _line_product_meta(line) -> tuple[str | None, str | None]:
    product = line.product
    goods_in_type = product.goods_in_type
    storage_regime = None
    try:
        storage_regime = product.technical.storage_regime
    except ObjectDoesNotExist:
        storage_regime = None
    return goods_in_type, storage_regime


def _dominant_header_key(lines) -> tuple[str | None, str | None]:
    """Prefer first line's type/regime; packaging wins if any line is packaging."""
    if not lines:
        return ProductGoodsInType.OTHER, None
    types = []
    for line in lines:
        gin_type, regime = _line_product_meta(line)
        if gin_type == ProductGoodsInType.PACKAGING:
            return ProductGoodsInType.PACKAGING, regime
        types.append((gin_type, regime))
    return types[0]


def resolve_goods_in_form(po_id: int) -> dict:
    try:
        po = get_purchase_order(po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise GoodsInFormError('Purchase order not found.') from exc

    lines = list(po.lines.all())
    # Ensure technical is available without N+1 when possible
    product_ids = [line.product_id for line in lines]
    if product_ids:
        from product.models import ProductTechnical
        tech_by_product = {
            row.product_id: row
            for row in ProductTechnical.objects.filter(product_id__in=product_ids)
        }
    else:
        tech_by_product = {}

    header_type, header_regime = _dominant_header_key(lines)
    # Recompute dominant with tech map for accuracy
    if lines:
        first = lines[0]
        header_type = first.product.goods_in_type or ProductGoodsInType.OTHER
        header_regime = None
        tech = tech_by_product.get(first.product_id)
        if tech is not None:
            header_regime = tech.storage_regime
        for line in lines:
            if line.product.goods_in_type == ProductGoodsInType.PACKAGING:
                header_type = ProductGoodsInType.PACKAGING
                tech = tech_by_product.get(line.product_id)
                header_regime = tech.storage_regime if tech else None
                break

    header_template = resolve_template(
        goods_in_type=header_type,
        storage_regime=header_regime,
        scope=GoodsInCheckScope.HEADER,
    )

    line_blocks = []
    for line in lines:
        gin_type = line.product.goods_in_type or ProductGoodsInType.OTHER
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
            'qty_ordered': str(line.qty_ordered),
            'qty_received': str(line.qty_received),
            'qty_balance': str(line.qty_balance),
            'pack_size': line.shape_format_label,
            'unit_id': line.unit_id,
            'unit_name': line.unit.name if line.unit_id else None,
            'saved_answers': line.line_checks or {},
            'line_check_ok': line.line_check_ok,
            'template': _template_block(line_template),
        })

    return {
        'purchase_order_id': po.id,
        'number': po.number,
        'status': po.status,
        'supplier_id': po.supplier_id,
        'supplier_name': po.supplier.name if po.supplier_id else None,
        'ship_to_location_id': po.ship_to_location_id,
        'expected_at': _iso_date(po.expected_at),
        'ordered_at': _iso_date(po.ordered_at),
        'delivery_trace_number': po.delivery_trace_number,
        'reject_delivery': po.reject_delivery,
        'vehicle_temperature': (
            str(po.vehicle_temperature)
            if po.vehicle_temperature is not None else None
        ),
        'saved_header_answers': po.header_checks or {},
        'header': _template_block(header_template),
        'lines': line_blocks,
    }
