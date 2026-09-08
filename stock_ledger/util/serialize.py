from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from hardware.services import codes_for_serials
from product.models import Product, ProductSupplier
from stock_ledger.models import (
    ProductionRun,
    StockBalance,
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
    StockLot,
    StockReservation,
    StockUnit,
    StockUnitConversion,
)
from stock_ledger.util import entry_labels
from stock_ledger.util.conversions import StockValidationError, stock_to_kg, stock_to_packs
from stock_ledger.util.product_supplier_lookup import product_supplier_for_entry
from users_rbac.models import RbacUser

BALANCE_SELECT_RELATED = (
    'location',
    'lot__product__product_class',
    'lot__product__range',
    'lot__product__unit',
    'lot__product__yield_data',
    'lot__product_supplier__outer_unit',
    'lot__product_supplier__inner_unit',
)


def _dec(value):
    return str(value) if value is not None else None


def _format_qty(value):
    """Trailing-zero strip for API payloads (views helper, not serialize._dec)."""
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'




def _pretty_qty(value: Decimal) -> str:
    text = format(Decimal(value).normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def supplier_pack_fields(
    stock_qty: Decimal,
    product: Product | None,
    mapping: ProductSupplier | None,
) -> dict:
    """Warehouse display: N bags + pack shape. Ledger qty stays in product.unit."""
    display_kg = (
        _dec(stock_to_kg(stock_qty, product)) if product is not None else None
    )
    fields = {
        'pack_quantity': None,
        'pack_unit_name': None,
        'shape_format_label': None,
        'display_kg': display_kg,
        'product_supplier_id': None,
        'supplier_code': None,
        'sage_product_code': None,
        'supplier_product_name': None,
    }
    if mapping is None or product is None:
        return fields
    fields['product_supplier_id'] = mapping.id
    fields['supplier_code'] = mapping.supplier_code
    fields['sage_product_code'] = mapping.sage_product_code
    fields['supplier_product_name'] = mapping.supplier_product_name
    fields['shape_format_label'] = mapping.shape_format_label
    fields['pack_unit_name'] = (
        mapping.outer_unit.name if mapping.outer_unit_id else None
    )
    try:
        fields['pack_quantity'] = _dec(
            stock_to_packs(stock_qty, mapping, product),
        )
    except StockValidationError:
        fields['pack_quantity'] = None
    return fields


def pack_breakdown_row(
    stock_qty: Decimal,
    product: Product | None,
    mapping: ProductSupplier | None,
    *,
    lot_id: int,
    trace_number: str,
) -> dict | None:
    """One lot pack line for remaining; None → treat qty as loose_kg."""
    pack = supplier_pack_fields(stock_qty, product, mapping)
    if pack['pack_quantity'] is None or not pack['pack_unit_name']:
        return None
    pretty = _pretty_qty(Decimal(pack['pack_quantity']))
    unit = pack['pack_unit_name']
    shape = pack['shape_format_label']
    label = f'{pretty} {unit} ({shape})' if shape else f'{pretty} {unit}'
    return {
        'label': label,
        'pack_quantity': pack['pack_quantity'],
        'pack_unit_name': pack['pack_unit_name'],
        'shape_format_label': pack['shape_format_label'],
        'display_kg': pack['display_kg'],
        'lot_id': lot_id,
        'trace_number': trace_number,
    }

def receipt_meta_by_lot_ids(lot_ids: set[int]) -> dict[int, dict]:
    """lot_id → supplier + po from purchase receipt (prefer one that has them)."""
    out: dict[int, dict] = {}
    if not lot_ids:
        return out

    for entry in (
        StockEntry.objects
        .filter(
            lot_id__in=lot_ids,
            entry_type=StockEntryType.RECEIPT,
        )
        .select_related('counterparty_location')
        .order_by('id')
    ):
        loc = entry.counterparty_location
        candidate = {
            'receipt_entry_id': entry.id,
            'supplier_id': loc.id if loc is not None else None,
            'supplier_name': loc.name if loc is not None else None,
            'po_number': entry.po_number or None,
        }
        existing = out.get(entry.lot_id)
        if existing is None:
            out[entry.lot_id] = candidate
            continue
        # Prefer a later/earlier receipt that actually has supplier or PO.
        if existing.get('supplier_id') is None and candidate['supplier_id'] is not None:
            existing['supplier_id'] = candidate['supplier_id']
            existing['supplier_name'] = candidate['supplier_name']
        if existing.get('po_number') is None and candidate['po_number'] is not None:
            existing['po_number'] = candidate['po_number']
    return out


def serialize_balance_row(
    balance: StockBalance,
    *,
    receipt_meta: dict | None = None,
    stickers: list[dict] | None = None,
) -> dict:
    product = balance.lot.product
    try:
        yield_factor = _dec(product.yield_data.yield_factor)
    except ObjectDoesNotExist:
        yield_factor = None
    meta = receipt_meta or {}
    pack = supplier_pack_fields(
        balance.quantity,
        product,
        getattr(balance.lot, 'product_supplier', None),
    )
    return {
        'lot_id': balance.lot_id,
        'product_id': balance.lot.product_id,
        'product_name': product.name if balance.lot.product_id else None,
        'recipe_code': product.recipe_code if balance.lot.product_id else None,
        'product_class_id': product.product_class_id,
        'product_class_name': (
            product.product_class.name if product.product_class_id else None
        ),
        'range_id': product.range_id,
        'range_name': product.range.name if product.range_id else None,
        'unit_id': product.unit_id,
        'unit_name': product.unit.name if product.unit_id else None,
        'yield_factor': yield_factor,
        'trace_number': balance.lot.trace_number,
        'production_date': (
            balance.lot.production_date.isoformat()
            if balance.lot.production_date
            else None
        ),
        'use_by': balance.lot.use_by.isoformat() if balance.lot.use_by else None,
        'location_id': balance.location_id,
        'location_name': balance.location.name if balance.location_id else None,
        'receipt_entry_id': meta.get('receipt_entry_id'),
        'supplier_id': meta.get('supplier_id'),
        'supplier_name': meta.get('supplier_name'),
        'po_number': meta.get('po_number'),
        'quantity': _dec(balance.quantity),
        'quantity_base': _dec(balance.quantity_base),
        'last_entry_id': balance.last_entry_id,
        'updated_at': balance.updated_at.isoformat() if balance.updated_at else None,
        'stickers': [
            {
                'entry_id': row['entry_id'],
                'entry_code': row['entry_code'],
                'quantity': _dec(row['quantity']),
            }
            for row in (stickers or [])
        ],
        **pack,
    }


def serialize_balance_rows(
    balances: list[StockBalance],
    *,
    receipt_meta: dict | None = None,
) -> list[dict]:
    from stock_ledger.util import stickers

    meta = receipt_meta or {}
    sticker_map = stickers.open_stickers_for_balances(balances)
    return [
        serialize_balance_row(
            balance,
            receipt_meta=meta.get(balance.lot_id),
            stickers=sticker_map.get((balance.lot_id, balance.location_id), []),
        )
        for balance in balances
    ]


def actor_names_for(user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    return {
        user.id: (user.display_name or user.username)
        for user in RbacUser.objects.filter(pk__in=user_ids).only(
            'id', 'display_name', 'username',
        )
    }


def _display_grams(
    stock_qty: Decimal,
    entry_unit_name: str | None,
    display_kg: str | None,
) -> str | None:
    if entry_unit_name and entry_unit_name.lower() == 'grams':
        return _dec(stock_qty)
    if display_kg is not None:
        return _dec(Decimal(display_kg) * 1000)
    return None


def manage_entry_detail(
    entry: StockEntry,
    *,
    actor_names: dict[int, str] | None = None,
) -> dict:
    """Rich row for Stock Management Tool preview (product, qty, actor, route)."""
    from stock_ledger.util.reports import movement_row

    lot = entry.lot
    product = lot.product if lot is not None else None
    mapping = product_supplier_for_entry(entry)
    stock_qty = abs(entry.quantity) if entry.quantity is not None else Decimal('0')
    pack = supplier_pack_fields(stock_qty, product, mapping)
    row = movement_row(entry)
    counterparty = entry.counterparty_location
    location = entry.location
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

    shape_format_id = lot.shape_format_id if lot is not None else None
    shape_format_name = (
        lot.shape_format.name
        if lot is not None and lot.shape_format_id and getattr(lot, 'shape_format', None)
        else None
    )
    outer_qty = inner_qty = multiplier = None
    outer_unit_name = inner_unit_name = shape_format_label = None
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

    names = actor_names or {}
    reversal = None
    try:
        reversal = entry.reversed_by
    except ObjectDoesNotExist:
        reversal = None

    return {
        **row,
        'entry_code': entry_labels.entry_code(entry.id),
        'from_location_id': from_loc.id if from_loc is not None else None,
        'from_location_name': from_loc.name if from_loc is not None else None,
        'to_location_id': to_loc.id if to_loc is not None else None,
        'to_location_name': to_loc.name if to_loc is not None else None,
        'supplier_id': entry.counterparty_location_id,
        'supplier_name': counterparty.name if counterparty is not None else None,
        'supplier_lot_code': lot.supplier_lot_code if lot is not None else None,
        'lot_origin': lot.origin if lot is not None else None,
        'shape_format_id': shape_format_id,
        'shape_format_name': shape_format_name,
        'shape_format_label': shape_format_label or pack.get('shape_format_label'),
        'shape_outer_qty': outer_qty,
        'shape_outer_unit_name': outer_unit_name,
        'shape_inner_qty': inner_qty,
        'shape_inner_unit_name': inner_unit_name,
        'shape_multiplier': multiplier,
        'entry_unit_id': entry.unit_id,
        'entry_unit_name': entry.unit.name if entry.unit_id else None,
        'quantity_abs': _dec(stock_qty),
        'display_grams': _display_grams(
            stock_qty,
            entry.unit.name if entry.unit_id else None,
            pack.get('display_kg'),
        ),
        'base_unit_factor': _dec(entry.base_unit_factor),
        'unit_cost': _dec(entry.unit_cost),
        'line_cost': _dec(entry.line_cost),
        'source_entry_id': entry.source_entry_id,
        'source_entry_code': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id
            else None
        ),
        'source_production_output_id': (
            entry.source_document_id
            if entry.entry_type == StockEntryType.PRODUCTION_CONSUMPTION
            else None
        ),
        'source_production_output_code': (
            entry_labels.entry_code(entry.source_document_id)
            if (
                entry.entry_type == StockEntryType.PRODUCTION_CONSUMPTION
                and entry.source_document_id
            )
            else None
        ),
        'reverses_entry_id': entry.reverses_entry_id,
        'reversal_entry_id': reversal.id if reversal is not None else None,
        'reversal_entry_code': (
            entry_labels.entry_code(reversal.id) if reversal is not None else None
        ),
        'is_reversed': reversal is not None,
        'override_reason': entry.override_reason,
        'authorised_by_user_id': entry.authorised_by_user_id,
        'actor_user_id': entry.actor_user_id,
        'actor_name': (
            names.get(entry.actor_user_id)
            if entry.actor_user_id is not None
            else None
        ) or entry.lan_username,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
        'device_serial': entry.device_serial,
        'source_document_line': entry.source_document_line,
    }


def load_balance_for_row(*, lot_id: int, location_id: int) -> StockBalance | None:
    return (
        StockBalance.objects.select_related(*BALANCE_SELECT_RELATED)
        .filter(lot_id=lot_id, location_id=location_id)
        .first()
    )


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
    from stock_ledger.util import stock_units

    return {
        'id': unit.id,
        'unit_serial': unit.unit_serial,
        'lot_id': unit.lot_id,
        'location_id': unit.location_id,
        'unit_id': unit.unit_id,
        'quantity_initial': _format_qty(unit.quantity_initial),
        'quantity_remaining': _format_qty(unit.quantity_remaining),
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
        'to_kg': _format_qty(row.to_kg),
        'source': row.source,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


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
        outer_qty = _format_qty(mapping.outer_qty)
        outer_unit_name = mapping.outer_unit.name if mapping.outer_unit_id else None
        inner_qty = _format_qty(mapping.inner_qty)
        inner_unit_name = mapping.inner_unit.name if mapping.inner_unit_id else None
        multiplier = _format_qty(mapping.multiplier)
        pack_unit_name = outer_unit_name
        if mapping.multiplier != 0 and product is not None:
            try:
                pack_quantity = _format_qty(
                    stock_to_packs(abs(entry.quantity), mapping, product),
                )
            except StockValidationError:
                pack_quantity = None
    display_kg = (
        _format_qty(stock_to_kg(abs(entry.quantity), product))
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
        'quantity': _format_qty(entry.quantity),
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
        'base_unit_factor': _format_qty(entry.base_unit_factor),
        'quantity_base': _format_qty(entry.quantity_base),
        'unit_cost': _format_qty(entry.unit_cost),
        'line_cost': _format_qty(entry.line_cost),
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


def audit_event_dict(
    entry: StockEntry,
    device_codes: dict | None = None,
    po_numbers: dict | None = None,
) -> dict:
    # Cycle: serialize → entry_posting → services → serialize
    from stock_ledger.util import entry_posting
    # Cycle: timeline → reports → serialize.supplier_pack_fields
    from stock_ledger.util.timeline import receive_group_key

    lot = entry.lot
    product = lot.product
    posting = entry_posting.get_posting(entry)
    codes = (
        device_codes
        if device_codes is not None
        else codes_for_serials([entry.device_serial])
    )
    mapping = product_supplier_for_entry(entry)
    pack = supplier_pack_fields(abs(entry.quantity), product, mapping)
    row = {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'at': entry.recorded_at.isoformat() if entry.recorded_at else None,
        'effective_at': entry.effective_at.isoformat() if entry.effective_at else None,
        'action': entry.entry_type,
        'quantity': _format_qty(entry.quantity),
        # Base unit (KG) amount: the only figure comparable across mixed
        # entry units (grams, Kg, Box) on the same product.
        'quantity_base': _format_qty(entry.quantity_base),
        'unit_id': entry.unit_id,
        'unit_name': entry.unit.name if entry.unit_id else None,
        'pack_quantity': pack.get('pack_quantity'),
        'pack_unit_name': pack.get('pack_unit_name'),
        'receive_group_key': receive_group_key(entry.idempotency_key),
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
        'po_number': (
            entry.po_number
            or (
                po_numbers.get(entry.source_document_id)
                if po_numbers and entry.source_document_type == 'po'
                else None
            )
        ),
        'remarks': entry.remarks,
        'reverses_entry_id': entry.reverses_entry_id,
        'source_entry_id': entry.source_entry_id,
        'source_entry_code': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id
            else None
        ),
        'actor_user_id': entry.actor_user_id,
        'lan_username': entry.lan_username,
        'source_workstation': entry.source_workstation,
        'source_workstation_ip': entry.source_workstation_ip,
        'device_serial': entry.device_serial,
        'device_code': codes.get(entry.device_serial),
        'posting_status': posting.status if posting is not None else None,
        'is_live': (
            posting is None
            or posting.status == StockEntryPostingStatus.POSTED
        ),
    }
    label = entry_labels.get_label(entry)
    if label is not None:
        row['label_status'] = label.status
    return row


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

def reservation_dict(row: StockReservation) -> dict:
    return {
        'id': row.id,
        'lot_id': row.lot_id,
        'location_id': row.location_id,
        'quantity': _format_qty(row.quantity),
        'unit_id': row.unit_id,
        'status': row.status,
        'source_document_type': row.source_document_type,
        'source_document_id': row.source_document_id,
        'source_document_line': row.source_document_line,
        'consumed_by_entry_id': row.consumed_by_entry_id,
        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
    }
