from decimal import Decimal

from locations.location_images import location_image_url
from product.goods_in import product_is_direct_consume
from purchasing.models import (
    LineShortfallReason,
    PurchaseOrder,
    PurchaseOrderLine,
    SHORTFALL_AWAIT_REASONS,
)
from purchasing.services.po_qty import queued_hold_by_line_no
from users_rbac.models import RbacUser


def _iso_date(value):
    return value.isoformat() if value else None


def _iso_dt(value):
    return value.isoformat() if value else None


def shortfall_reason_options() -> list[dict]:
    return [
        {
            'code': code,
            'label': label,
            'closes_line': code not in SHORTFALL_AWAIT_REASONS,
            'needs_credit_note': code not in SHORTFALL_AWAIT_REASONS,
        }
        for code, label in LineShortfallReason.choices
    ]


def _qty_str(value) -> str | None:
    """2.000000 → '2', 2.500000 → '2.5' (no trailing zeros)."""
    if value is None:
        return None
    text = f'{Decimal(str(value)):f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def rbac_names(user_ids: set[int | None]) -> dict[int, str]:
    return {
        uid: actor['name']
        for uid, actor in rbac_actors(user_ids).items()
    }


def rbac_actors(user_ids: set[int | None]) -> dict[int, dict]:
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    return {
        u.id: {
            'user_id': u.id,
            'sub': u.cognito_sub,
            'name': u.display_name or u.username,
            'email': u.email,
        }
        for u in RbacUser.objects.filter(pk__in=ids).only(
            'id', 'display_name', 'username', 'email', 'cognito_sub',
        )
    }


def line_dict(line: PurchaseOrderLine, qty_queued=None) -> dict:
    if qty_queued is None:
        qty_queued = queued_hold_by_line_no(line.purchase_order_id).get(
            line.line_no, 0,
        )
    return {
        'id': line.id,
        'line_no': line.line_no,
        'product_id': line.product_id,
        'product_name': line.product.name if line.product_id else None,
        'product_supplier_id': line.product_supplier_id,
        'direct_consume': (
            product_is_direct_consume(line.product) if line.product_id else False
        ),
        'qty_ordered': _qty_str(line.qty_ordered),
        'qty_received': _qty_str(line.qty_received),
        'qty_rejected': _qty_str(line.qty_rejected),
        'qty_queued': _qty_str(qty_queued),
        'qty_balance': _qty_str(line.qty_balance),
        'shortfall_reason': line.shortfall_reason,
        'needs_credit_note': line.qty_rejected > 0,
        'unit_id': line.unit_id,
        'unit_name': line.unit.name if line.unit_id else None,
        'unit_cost': _qty_str(line.unit_cost),
        'multiplier': _qty_str(line.multiplier),
        'shape_format_label': line.shape_format_label,
        'production_date': _iso_date(line.production_date),
        'use_by': _iso_date(line.use_by),
        'trace_number': line.trace_number,
        'product_temperature': _qty_str(line.product_temperature),
        'line_check_ok': line.line_check_ok,
        'line_closed': line.line_closed,
        'stock_in_done': line.stock_in_done,
        'label_format': line.label_format,
        'label_count': line.label_count,
        'remarks': line.remarks,
    }


def po_list_dict(po: PurchaseOrder) -> dict:
    image_url = location_image_url(po.supplier) if po.supplier_id else None
    return {
        'id': po.id,
        # Internal system number (PO{id}). Prefer sage_po_number on UI.
        'number': po.number,
        'sage_po_number': po.external_number,
        'external_number': po.external_number,
        'status': po.status,
        'revision_no': po.revision_no,
        'supplier_id': po.supplier_id,
        'supplier_name': po.supplier.name if po.supplier_id else None,
        'supplier_image_url': image_url,
        'image_url': image_url,
        'image': image_url,
        'logo': image_url,
        'ship_to_location_id': po.ship_to_location_id,
        'ship_to_location_name': (
            po.ship_to_location.name if po.ship_to_location_id else None
        ),
        'ordered_at': _iso_date(po.ordered_at),
        'expected_at': _iso_date(po.expected_at),
        'delivery_at': _iso_date(po.delivery_at),
        'source': po.source,
        'reject_delivery': po.reject_delivery,
        'created_at': _iso_dt(po.created_at),
        'updated_at': _iso_dt(po.updated_at),
    }


def po_detail_dict(po: PurchaseOrder) -> dict:
    names = rbac_names({
        po.checked_by_user_id,
        po.qc_tl_checked_by_user_id,
        po.created_by_user_id,
    })
    data = po_list_dict(po)
    holds = queued_hold_by_line_no(po.id)
    data.update({
        'remarks': po.remarks,
        'delivery_trace_number': po.delivery_trace_number,
        'vehicle_temperature': (
            _qty_str(po.vehicle_temperature)
            if po.vehicle_temperature is not None else None
        ),
        'header_checks': po.header_checks,
        'checked_by_user_id': po.checked_by_user_id,
        'checked_by_name': names.get(po.checked_by_user_id),
        'checked_at': _iso_dt(po.checked_at),
        'qc_tl_checked_by_user_id': po.qc_tl_checked_by_user_id,
        'qc_tl_checked_by_name': names.get(po.qc_tl_checked_by_user_id),
        'qc_tl_checked_at': _iso_dt(po.qc_tl_checked_at),
        'qc_tl_comment': po.qc_tl_comment,
        'total_net': _qty_str(po.total_net),
        'created_by_user_id': po.created_by_user_id,
        'created_by_name': names.get(po.created_by_user_id),
        'lines': [
            line_dict(line, qty_queued=holds.get(line.line_no, 0))
            for line in po.lines.all()
        ],
    })
    return data
