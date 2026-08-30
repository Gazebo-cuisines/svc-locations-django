"""Stock Adjustment — add qty with labels/queue, no QC (Chunk 4)."""

from datetime import date, datetime

from django.db import transaction

from locations.models import Location
from product.models import Product
from purchasing.serialize import _qty_str
from purchasing.services.adhoc_goods_in import (
    AdhocGoodsInError,
    _entry_dict,
    _label_plan,
    _parse_qty,
    _resolve_shape,
    _split_quantities,
    _stock_unit_dicts,
)
from stock_ledger.models import StockLotOrigin
from stock_ledger.util import entry_labels
from stock_ledger.util import entry_posting
from stock_ledger.util import services as stock_services
from stock_ledger.util import stock_units
from stock_ledger.util.conversions import StockValidationError


class StockAdjustmentError(ValueError):
    pass


def _optional_date(value, field_name: str) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise StockAdjustmentError(
            f'Invalid date for {field_name}. Use YYYY-MM-DD.',
        ) from exc


def _wrap(exc: Exception) -> StockAdjustmentError:
    return StockAdjustmentError(str(exc))


@transaction.atomic
def receive_stock_adjustment(
    *,
    body: dict,
    audit: dict | None = None,
) -> dict:
    """
    One-shot add stock: product + shape/qty + manual trace/use_by + labels.
    No AdhocGoodsInSession / QC. Never touches PurchaseOrder.
    """
    audit = dict(audit or {})
    product_id = body.get('product_id')
    location_id = body.get('location_id')
    if product_id in (None, '') or location_id in (None, ''):
        raise StockAdjustmentError('product_id and location_id are required.')
    try:
        product_id = int(product_id)
        location_id = int(location_id)
    except (TypeError, ValueError) as exc:
        raise StockAdjustmentError(
            'product_id and location_id must be integers.',
        ) from exc

    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist as exc:
        raise StockAdjustmentError('Product not found.') from exc
    if not Location.objects.filter(pk=location_id).exists():
        raise StockAdjustmentError(f'location_id={location_id} not found.')

    idempotency_key = body.get('idempotency_key')
    if idempotency_key in (None, ''):
        raise StockAdjustmentError('idempotency_key is required.')
    idempotency_key = str(idempotency_key)

    trace_number = body.get('trace_number')
    if trace_number in (None, '') or not str(trace_number).strip():
        raise StockAdjustmentError('trace_number is required (manual entry).')
    trace_number = str(trace_number).strip()

    use_by = _optional_date(body.get('use_by'), 'use_by')
    if use_by is None:
        raise StockAdjustmentError('use_by is required.')
    production_date = _optional_date(body.get('production_date'), 'production_date')

    try:
        label_format, label_count = _label_plan(body)
        mapping, receipt_qty, stock_qty, shape_label, shape_other, unit_id = (
            _resolve_shape(product=product, body=body)
        )
        qty_parts = _split_quantities(receipt_qty, label_count)
    except AdhocGoodsInError as exc:
        raise _wrap(exc) from exc
    except StockValidationError as exc:
        raise _wrap(exc) from exc

    unit_keys = (
        [idempotency_key]
        if label_count == 1
        else [f'{idempotency_key}:u:{i}' for i in range(1, label_count + 1)]
    )

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
            raise StockAdjustmentError('supplier_id must be an integer.') from exc
        if not Location.objects.filter(pk=supplier_id).exists():
            raise StockAdjustmentError(f'supplier_id={supplier_id} not found.')

    try:
        lot = stock_services.resolve_lot(
            product_id=product.id,
            trace_number=trace_number,
            use_by=use_by,
            production_date=production_date,
            origin=StockLotOrigin.PURCHASE,
            product_supplier_id=mapping.id if mapping else None,
            shape_format_id=(
                mapping.purchase_shape_format_id if mapping else None
            ),
        )
    except StockValidationError as exc:
        raise _wrap(exc) from exc

    remarks = body.get('remarks')
    receipt_audit = {
        'actor_user_id': (
            audit.get('actor_user_id')
            or body.get('actor_user_id')
            or body.get('checked_by_user_id')
        ),
        'lan_username': audit.get('lan_username') or body.get('lan_username'),
        'source_workstation': audit.get('source_workstation'),
        'source_workstation_ip': audit.get('source_workstation_ip'),
        'remarks': str(remarks) if remarks not in (None, '') else None,
        'source_document_type': 'stock_adjustment',
        'counterparty_location_id': supplier_id,
    }
    receipt_audit = {k: v for k, v in receipt_audit.items() if v is not None}

    transactions = []
    last_entry = None
    for unit_index, (unit_key, part_qty) in enumerate(
        zip(unit_keys, qty_parts), start=1,
    ):
        try:
            entry = stock_services.receipt(
                idempotency_key=unit_key,
                lot=lot,
                location_id=location_id,
                quantity=part_qty,
                unit_id=unit_id,
                product_supplier=mapping,
                defer_balance=queue_stock,
                **receipt_audit,
            )
        except StockValidationError as exc:
            raise _wrap(exc) from exc
        last_entry = entry
        tx = {
            'unit_index': unit_index,
            'stock_entry_id': entry.id,
            'entry_code': entry_labels.entry_code(entry.id),
            'quantity_stock': _qty_str(entry.quantity),
            'lot_id': lot.id,
            'idempotency_key': unit_key,
            'entry': _entry_dict(entry),
        }
        if queue_stock:
            posting = entry_posting.queue_entry(
                entry=entry,
                actor_user_id=receipt_audit.get('actor_user_id'),
                lan_username=receipt_audit.get('lan_username'),
                source_workstation=receipt_audit.get('source_workstation'),
                meta={
                    'stock_adjustment': True,
                    'shape_format_label': shape_label,
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
                    raise StockAdjustmentError(
                        'print_unit_count and print_quantity_per_unit '
                        'are both required to print labels.',
                    )
                prefix = (
                    body.get('print_idempotency_key_prefix')
                    or f'{unit_key}:print'
                )
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

    return {
        'product_id': product.id,
        'product_name': product.name,
        'location_id': location_id,
        'trace_number': trace_number,
        'use_by': use_by.isoformat(),
        'production_date': (
            production_date.isoformat() if production_date else None
        ),
        'product_supplier_id': mapping.id if mapping else None,
        'shape_format_label': shape_label,
        'shape_other': shape_other,
        'qty_entered': str(_parse_qty(body.get('quantity'), 'quantity')),
        'stock_qty': _qty_str(stock_qty),
        'label_format': label_format,
        'label_count': label_count,
        'stock_entry_id': last_entry.id if last_entry else None,
        'receive_results': transactions,
    }
