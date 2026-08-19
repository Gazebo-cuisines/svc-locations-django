from decimal import Decimal

from hardware.services import codes_for_serials
from product.models import ProductSupplier
from stock_ledger.models import (
    ProductionRun,
    StockEntry,
    StockEntryType,
    StockLot,
    StockReservation,
    StockUnit,
    StockUnitConversion,
)
from stock_ledger.util import entry_labels, stock_units
from stock_ledger.util.allocation_status import STATUS_COMPLETE
from stock_ledger.util.conversions import (
    StockValidationError,
    stock_to_kg,
    stock_to_packs,
)
from stock_ledger.util.parse import format_display_date


def _dec(value):
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def lot_dict(lot: StockLot) -> dict:
    return {
        'id': lot.id,
        'product_id': lot.product_id,
        'recipe_version_id': lot.recipe_version_id,
        'shape_format_id': lot.shape_format_id,
        'product_supplier_id': lot.product_supplier_id,
        'trace_number': lot.trace_number,
        'supplier_lot_code': lot.supplier_lot_code,
        'origin': lot.origin,
        'production_date': (
            lot.production_date.isoformat() if lot.production_date else None
        ),
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'created_at': lot.created_at.isoformat() if lot.created_at else None,
    }


def stock_unit_dict(unit: StockUnit) -> dict:
    return {
        'id': unit.id,
        'unit_serial': unit.unit_serial,
        'lot_id': unit.lot_id,
        'location_id': unit.location_id,
        'unit_id': unit.unit_id,
        'quantity_initial': _dec(unit.quantity_initial),
        'quantity_remaining': _dec(unit.quantity_remaining),
        'status': unit.status,
        'created_by_entry_id': unit.created_by_entry_id,
        'created_at': unit.created_at.isoformat() if unit.created_at else None,
        'voided_at': unit.voided_at.isoformat() if unit.voided_at else None,
        'void_reason': unit.void_reason,
        'gs1': stock_units.build_gs1_payload(unit),
    }


def unit_conversion_dict(row: StockUnitConversion) -> dict:
    return {
        'id': row.id,
        'unit_id': row.unit_id,
        'product_id': row.product_id,
        'to_kg': _dec(row.to_kg),
        'source': row.source,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def product_supplier_for_lot(lot):
    if lot is None:
        return None
    if lot.product_supplier_id:
        return (
            ProductSupplier.objects
            .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
            .filter(pk=lot.product_supplier_id)
            .first()
        )
    return (
        ProductSupplier.objects
        .filter(product_id=lot.product_id, is_active=True)
        .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
        .order_by('-is_default', '-id')
        .first()
    )


def product_supplier_for_entry(entry: StockEntry):
    """Shape mapping stamped on the lot, else best-effort for receipts."""
    lot = entry.lot
    hit = product_supplier_for_lot(lot)
    if hit is not None:
        return hit
    if entry.entry_type != StockEntryType.RECEIPT:
        return None
    if lot is None or entry.counterparty_location_id is None:
        return None
    qs = (
        ProductSupplier.objects
        .filter(
            product_id=lot.product_id,
            supplier_id=entry.counterparty_location_id,
            is_active=True,
        )
        .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
    )
    if lot.shape_format_id:
        shaped = qs.filter(purchase_shape_format_id=lot.shape_format_id).first()
        if shaped is not None:
            return shaped
    return qs.filter(is_default=True).first() or qs.order_by('-id').first()


def entry_dict(entry: StockEntry) -> dict:
    counterparty = entry.counterparty_location
    location = entry.location
    lot = entry.lot
    product = lot.product if lot is not None else None
    unit = entry.unit
    # transfer_out / issue: location → counterparty; transfer_in: counterparty → location;
    # receipt: supplier (counterparty) → location
    if entry.entry_type == StockEntryType.TRANSFER_IN:
        from_loc, to_loc = counterparty, location
    elif entry.entry_type in (
        StockEntryType.TRANSFER_OUT,
        StockEntryType.ISSUE,
        StockEntryType.DISPOSAL,
    ):
        from_loc, to_loc = location, counterparty
    else:
        from_loc, to_loc = counterparty, location
    mapping = product_supplier_for_entry(entry)
    pack_quantity = None
    pack_unit_name = None
    shape_format_label = None
    shape_format_id = lot.shape_format_id if lot is not None else None
    shape_format_name = (
        lot.shape_format.name
        if lot is not None and lot.shape_format_id and getattr(lot, 'shape_format', None)
        else None
    )
    outer_qty = None
    outer_unit_name = None
    inner_qty = None
    inner_unit_name = None
    multiplier = None
    if mapping is not None:
        shape_format_label = mapping.shape_format_label
        if mapping.purchase_shape_format_id:
            shape_format_id = mapping.purchase_shape_format_id
            if mapping.purchase_shape_format is not None:
                shape_format_name = mapping.purchase_shape_format.name
        outer_qty = _dec(mapping.outer_qty)
        outer_unit_name = mapping.outer_unit.name if mapping.outer_unit_id else None
        inner_qty = _dec(mapping.inner_qty)
        inner_unit_name = mapping.inner_unit.name if mapping.inner_unit_id else None
        multiplier = _dec(mapping.multiplier)
        pack_unit_name = outer_unit_name
        if mapping.multiplier != 0 and product is not None:
            try:
                pack_quantity = _dec(
                    stock_to_packs(abs(entry.quantity), mapping, product),
                )
            except StockValidationError:
                pack_quantity = None
    display_kg = (
        _dec(stock_to_kg(abs(entry.quantity), product))
        if product is not None else None
    )
    return {
        'id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'idempotency_key': entry.idempotency_key,
        'entry_type': entry.entry_type,
        'lot_id': entry.lot_id,
        'trace_number': lot.trace_number if lot is not None else None,
        'supplier_lot_code': lot.supplier_lot_code if lot is not None else None,
        'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
        'production_date': (
            lot.production_date.isoformat()
            if lot is not None and lot.production_date
            else None
        ),
        'product_id': product.id if product is not None else None,
        'product_name': product.name if product is not None else None,
        'location_id': entry.location_id,
        'location_name': location.name if location is not None else None,
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            counterparty.name if counterparty is not None else None
        ),
        'from_location_id': from_loc.id if from_loc is not None else None,
        'from_location_name': from_loc.name if from_loc is not None else None,
        'to_location_id': to_loc.id if to_loc is not None else None,
        'to_location_name': to_loc.name if to_loc is not None else None,
        'supplier_id': entry.counterparty_location_id,
        'supplier_name': counterparty.name if counterparty is not None else None,
        'transfer_group_id': entry.transfer_group_id,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'unit_name': unit.name if unit is not None else None,
        'pack_quantity': pack_quantity,
        'pack_unit_name': pack_unit_name,
        'display_kg': display_kg,
        'shape_format_id': shape_format_id,
        'shape_format_name': shape_format_name,
        'shape_format_label': shape_format_label,
        'shape_outer_qty': outer_qty,
        'shape_outer_unit_name': outer_unit_name,
        'shape_inner_qty': inner_qty,
        'shape_inner_unit_name': inner_unit_name,
        'shape_multiplier': multiplier,
        'product_supplier_id': mapping.id if mapping is not None else None,
        'base_unit_factor': _dec(entry.base_unit_factor),
        'quantity_base': _dec(entry.quantity_base),
        'unit_cost': _dec(entry.unit_cost),
        'line_cost': _dec(entry.line_cost),
        'period_id': entry.period_id,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'recorded_at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'reverses_entry_id': entry.reverses_entry_id,
        'source_entry_id': entry.source_entry_id,
        'source_entry_code': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id
            else None
        ),
        'override_reason': entry.override_reason,
        'authorised_by_user_id': entry.authorised_by_user_id,
        'po_number': entry.po_number,
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'source_document_line': entry.source_document_line,
        'actor_user_id': entry.actor_user_id,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
        'device_serial': entry.device_serial,
        'device_code': codes_for_serials([entry.device_serial]).get(entry.device_serial),
        'remarks': entry.remarks,
        'entry_hash': entry.entry_hash,
        'prev_hash': entry.prev_hash,
    }


def audit_event_dict(entry: StockEntry) -> dict:
    lot = entry.lot
    product = lot.product
    return {
        'entry_id': entry.id,
        'at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'action': entry.entry_type,
        'quantity': _dec(entry.quantity),
        # Base unit (KG) amount: the only figure comparable across mixed
        # entry units (grams, Kg, Box) on the same product.
        'quantity_base': _dec(entry.quantity_base),
        'unit_id': entry.unit_id,
        'unit_name': entry.unit.name if entry.unit_id else None,
        'product_id': product.id,
        'product_name': product.name,
        'lot_id': lot.id,
        'trace_number': lot.trace_number,
        'use_by': lot.use_by.isoformat() if lot.use_by else None,
        'location_id': entry.location_id,
        'location_name': entry.location.name if entry.location_id else None,
        'counterparty_location_id': entry.counterparty_location_id,
        'counterparty_location_name': (
            entry.counterparty_location.name
            if entry.counterparty_location_id
            else None
        ),
        'source_document_type': entry.source_document_type,
        'source_document_id': entry.source_document_id,
        'source_document_line': entry.source_document_line,
        'po_number': entry.po_number,
        'remarks': entry.remarks,
        'reverses_entry_id': entry.reverses_entry_id,
        'actor_user_id': entry.actor_user_id,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
        'device_serial': entry.device_serial,
        'device_code': codes_for_serials([entry.device_serial]).get(entry.device_serial),
    }


def production_run_dict(run: ProductionRun) -> dict:
    return {
        'id': run.id,
        'stock_entry_id': run.stock_entry_id,
        'resource_id': run.resource_id,
        'shift_code': run.shift_code,
        'staff_count': run.staff_count,
        'base_date': run.base_date.isoformat() if run.base_date else None,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'created_at': run.created_at.isoformat() if run.created_at else None,
    }


def allocation_fields(status: dict | None) -> dict:
    if status is None:
        return {
            'allocation_status': STATUS_COMPLETE,
            'incomplete_reasons': [],
            'remaining_component_count': 0,
        }
    return {
        'allocation_status': status['allocation_status'],
        'incomplete_reasons': status['incomplete_reasons'],
        'remaining_component_count': status['remaining_component_count'],
    }


def production_list_row(
    run: ProductionRun,
    *,
    allocation: dict | None = None,
) -> dict:
    entry = run.stock_entry
    lot = entry.lot
    product = lot.product
    row = {
        'entry_id': entry.id,
        'run_id': run.id,
        'base_date': format_display_date(run.base_date),
        'base_date_iso': run.base_date.isoformat() if run.base_date else None,
        'from_location_id': entry.counterparty_location_id,
        'from_location_name': (
            entry.counterparty_location.name
            if entry.counterparty_location_id
            else None
        ),
        'to_location_id': entry.location_id,
        'to_location_name': entry.location.name if entry.location_id else None,
        'product_id': product.id,
        'product_name': product.name,
        'recipe_code': product.recipe_code,
        'quantity': _dec(entry.quantity),
        'unit_id': entry.unit_id,
        'unit_name': entry.unit.name if entry.unit_id else None,
        'resource_id': run.resource_id,
        'resource_name': run.resource.name if run.resource_id else None,
        'shift_code': run.shift_code,
        'staff_count': run.staff_count,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'use_by': format_display_date(lot.use_by),
        'use_by_iso': lot.use_by.isoformat() if lot.use_by else None,
        'trace_number': lot.trace_number,
        'recipe_version_id': lot.recipe_version_id,
    }
    row.update(allocation_fields(allocation))
    return row


def reservation_dict(row: StockReservation) -> dict:
    return {
        'id': row.id,
        'lot_id': row.lot_id,
        'location_id': row.location_id,
        'quantity': _dec(row.quantity),
        'unit_id': row.unit_id,
        'status': row.status,
        'source_document_type': row.source_document_type,
        'source_document_id': row.source_document_id,
        'source_document_line': row.source_document_line,
        'consumed_by_entry_id': row.consumed_by_entry_id,
        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
    }
