from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from product.models import Product, ProductSupplier
from stock_ledger.models import StockBalance, StockEntry, StockEntryType
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
        **pack,
    }


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
