from purchasing.models import PurchaseOrder, PurchaseOrderLine


def _iso_date(value):
    return value.isoformat() if value else None


def _iso_dt(value):
    return value.isoformat() if value else None


def line_dict(line: PurchaseOrderLine) -> dict:
    return {
        'id': line.id,
        'line_no': line.line_no,
        'product_id': line.product_id,
        'product_name': line.product.name if line.product_id else None,
        'product_supplier_id': line.product_supplier_id,
        'qty_ordered': str(line.qty_ordered),
        'qty_received': str(line.qty_received),
        'qty_balance': str(line.qty_balance),
        'unit_id': line.unit_id,
        'unit_name': line.unit.name if line.unit_id else None,
        'unit_cost': str(line.unit_cost) if line.unit_cost is not None else None,
        'multiplier': str(line.multiplier) if line.multiplier is not None else None,
        'shape_format_label': line.shape_format_label,
        'production_date': _iso_date(line.production_date),
        'use_by': _iso_date(line.use_by),
        'trace_number': line.trace_number,
        'product_temperature': (
            str(line.product_temperature)
            if line.product_temperature is not None else None
        ),
        'line_check_ok': line.line_check_ok,
        'line_closed': line.line_closed,
        'stock_in_done': line.stock_in_done,
        'remarks': line.remarks,
    }


def po_list_dict(po: PurchaseOrder) -> dict:
    return {
        'id': po.id,
        'number': po.number,
        'status': po.status,
        'supplier_id': po.supplier_id,
        'supplier_name': po.supplier.name if po.supplier_id else None,
        'ship_to_location_id': po.ship_to_location_id,
        'ship_to_location_name': (
            po.ship_to_location.name if po.ship_to_location_id else None
        ),
        'ordered_at': _iso_date(po.ordered_at),
        'expected_at': _iso_date(po.expected_at),
        'source': po.source,
        'external_number': po.external_number,
        'reject_delivery': po.reject_delivery,
        'created_at': _iso_dt(po.created_at),
        'updated_at': _iso_dt(po.updated_at),
    }


def po_detail_dict(po: PurchaseOrder) -> dict:
    data = po_list_dict(po)
    data.update({
        'remarks': po.remarks,
        'delivery_trace_number': po.delivery_trace_number,
        'vehicle_temperature': (
            str(po.vehicle_temperature)
            if po.vehicle_temperature is not None else None
        ),
        'header_checks': po.header_checks,
        'checked_by_user_id': po.checked_by_user_id,
        'checked_at': _iso_dt(po.checked_at),
        'qc_tl_checked_by_user_id': po.qc_tl_checked_by_user_id,
        'qc_tl_checked_at': _iso_dt(po.qc_tl_checked_at),
        'qc_tl_comment': po.qc_tl_comment,
        'total_net': str(po.total_net) if po.total_net is not None else None,
        'created_by_user_id': po.created_by_user_id,
        'lines': [line_dict(line) for line in po.lines.all()],
    })
    return data
