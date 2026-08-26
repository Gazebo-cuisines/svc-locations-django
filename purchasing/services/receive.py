from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from locations.models import Location
from purchasing.models import (
    LineShortfallReason,
    PurchaseOrder,
    PurchaseOrderDeliveryStatus,
    PurchaseOrderHistoryEvent,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SHORTFALL_AWAIT_REASONS,
)
from purchasing.services.delivery import (
    DeliveryError,
    get_or_create_delivery_line,
    resolve_delivery_for_receive,
    sync_po_from_delivery,
)
from purchasing.services.po_qty import (
    queued_hold_for_line,
    recompute_po_status,
)
from purchasing.serialize import _qty_str
from purchasing.services.goods_in_form import resolve_goods_in_form
from purchasing.services.timeline import actor_json, record_history
from purchasing.services.release import is_quarantine_location
from stock_ledger.models import StockEntry, StockLotOrigin
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util import entry_labels
from stock_ledger.util import entry_posting
from stock_ledger.util import services as stock_services
from stock_ledger.util import stock_units

class ReceiveError(ValueError):
    pass


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReceiveError(f'Invalid decimal for {field_name}.') from exc
    if qty <= 0:
        raise ReceiveError(f'{field_name} must be greater than 0.')
    return qty


def _parse_shortfall(raw: dict, index: int, leftover: Decimal) -> str | None:
    if leftover <= 0:
        return None
    code = raw.get('shortfall_reason')
    if code in (None, ''):
        raise ReceiveError(
            f'lines[{index}].shortfall_reason is required when quantity '
            f'is less than balance.',
        )
    code = str(code)
    valid = {choice.value for choice in LineShortfallReason}
    if code not in valid:
        raise ReceiveError(
            f'lines[{index}].shortfall_reason must be one of: '
            f'{", ".join(sorted(valid))}.',
        )
    remarks = str(raw.get('remarks') or '').strip()
    if code == LineShortfallReason.OTHER and not remarks:
        raise ReceiveError(
            f'lines[{index}].remarks is required when shortfall_reason=other.',
        )
    return code


def _optional_date(value, field_name: str) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ReceiveError(
            f'Invalid date for {field_name}. Use YYYY-MM-DD.',
        ) from exc


def _stock_quantity(line: PurchaseOrderLine, purchase_qty: Decimal) -> Decimal:
    """Purchase qty × pack multiplier → stock qty (when no product_supplier)."""
    multiplier = line.multiplier if line.multiplier is not None else Decimal('1')
    if multiplier <= 0:
        raise ReceiveError(f'line {line.line_no} has invalid multiplier.')
    return (purchase_qty * multiplier).quantize(Decimal('0.000001'))


def _entry_payload(entry: StockEntry) -> dict:
    # Lazy: avoid import cycle at module load (views → … → receive).
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


def _wants_queued_stock(raw: dict, *, label_format: str | None) -> bool:
    flag = raw.get('queue_stock')
    if flag in (True, 'true', '1', 'yes', 'True'):
        return True
    if flag in (False, 'false', '0', 'no', 'False'):
        return False
    return label_format not in (None, '')


def _split_quantities(total: Decimal, parts: int) -> list[Decimal]:
    if parts < 1:
        raise ReceiveError('label_count must be >= 1.')
    if parts == 1:
        return [total]
    base = (total / parts).quantize(Decimal('0.000001'))
    chunks = [base] * (parts - 1)
    last = (total - sum(chunks)).quantize(Decimal('0.000001'))
    if last <= 0:
        raise ReceiveError(
            f'Cannot split quantity {total} into {parts} label transactions.',
        )
    chunks.append(last)
    return chunks


def _resolve_label_plan(line: PurchaseOrderLine, raw: dict, index: int) -> tuple[str | None, int]:
    """Admin line wins; warehouse body only used if line has no label plan."""
    if line.label_format not in (None, ''):
        fmt = str(line.label_format).strip().lower()
        count = int(line.label_count or (1 if fmt == 'pallet' else 0))
    elif raw.get('label_format') not in (None, ''):
        fmt = str(raw.get('label_format')).strip().lower()
        if raw.get('label_count') in (None, ''):
            count = 1 if fmt == 'pallet' else 0
        else:
            count = int(raw.get('label_count'))
    else:
        return None, 1
    if fmt not in ('pallet', 'box'):
        raise ReceiveError(
            f'lines[{index}].label_format must be pallet or box.',
        )
    if count < 1:
        raise ReceiveError(
            f'lines[{index}]: label_count is required for label_format={fmt}.',
        )
    if fmt == 'pallet' and count != 1:
        raise ReceiveError(
            f'lines[{index}]: pallet requires label_count=1.',
        )
    return fmt, count


def _unit_idempotency_keys(base: str, count: int) -> list[str]:
    if count == 1:
        return [base]
    return [f'{base}:u:{i}' for i in range(1, count + 1)]


def _print_units_for_line(
    *,
    raw: dict,
    index: int,
    entry: StockEntry,
    idempotency_key: str,
    audit: dict,
) -> list:
    from stock_ledger.views import stock_unit_dict

    print_count = raw.get('print_unit_count')
    print_qty = raw.get('print_quantity_per_unit')
    if print_count in (None, '') and print_qty in (None, ''):
        return []
    if print_count in (None, '') or print_qty in (None, ''):
        raise ReceiveError(
            f'lines[{index}]: print_unit_count and print_quantity_per_unit '
            f'are both required to print labels.',
        )
    prefix = raw.get('print_idempotency_key_prefix')
    if prefix in (None, ''):
        prefix = f'{idempotency_key}:print'
    units = stock_units.create_units_for_entry(
        source_entry=entry,
        unit_count=int(print_count),
        quantity_per_unit=_parse_decimal(
            print_qty, f'lines[{index}].print_quantity_per_unit',
        ),
        idempotency_key_prefix=str(prefix),
        actor_user_id=audit.get('actor_user_id'),
        lan_username=audit.get('lan_username'),
        source_workstation=audit.get('source_workstation'),
    )
    return [stock_unit_dict(u) for u in units]


@transaction.atomic
def receive_purchase_order(
    po_id: int,
    *,
    body: dict,
    audit: dict | None = None,
    delivery_id: int | None = None,
) -> dict:
    audit = dict(audit or {})
    try:
        po = (
            PurchaseOrder.objects.select_for_update()
            .select_related('supplier')
            .get(pk=po_id)
        )
    except PurchaseOrder.DoesNotExist as exc:
        raise ReceiveError('Purchase order not found.') from exc

    if po.status not in (
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.PARTIAL,
    ):
        raise ReceiveError(
            f'Receive only allowed when status is ordered or partial '
            f'(current={po.status}).',
        )
    try:
        delivery = resolve_delivery_for_receive(po, delivery_id=delivery_id)
    except DeliveryError as exc:
        raise ReceiveError(str(exc)) from exc
    if delivery.reject_delivery:
        raise ReceiveError('Cannot receive a rejected delivery.')
    if delivery.checked_at is None:
        raise ReceiveError('Complete header QC before receive.')

    location_id = body.get('location_id') or (
        po.ship_to_location_id if po.ship_to_location_id else None
    )
    if location_id in (None, ''):
        raise ReceiveError('location_id is required.')
    try:
        location_id = int(location_id)
    except (TypeError, ValueError) as exc:
        raise ReceiveError('location_id must be an integer.') from exc
    if not Location.objects.filter(pk=location_id).exists():
        raise ReceiveError(f'location_id={location_id} not found.')

    quarantine = bool(body.get('quarantine'))
    if quarantine and not is_quarantine_location(location_id):
        raise ReceiveError(
            f'location_id={location_id} must be a quarantine location when quarantine=true.',
        )

    raw_lines = body.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ReceiveError('lines must be a non-empty list.')

    results = []
    did_receive = False
    for index, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            raise ReceiveError(f'lines[{index}] must be an object.')
        line_id = raw.get('line_id')
        if line_id in (None, ''):
            raise ReceiveError(f'lines[{index}].line_id is required.')
        try:
            line = (
                PurchaseOrderLine.objects.select_for_update(of=('self',))
                .select_related(
                    'product',
                    'product_supplier',
                    'product_supplier__outer_unit',
                    'product_supplier__inner_unit',
                    'product_supplier__purchase_shape_format',
                )
                .get(pk=int(line_id), purchase_order_id=po.id)
            )
        except (PurchaseOrderLine.DoesNotExist, TypeError, ValueError) as exc:
            raise ReceiveError(
                f'lines[{index}].line_id={line_id} not found on this PO.',
            ) from exc

        dline = get_or_create_delivery_line(delivery, line)
        if not dline.line_check_ok:
            raise ReceiveError(
                f'Line {line.line_no} has not passed QC (line_check_ok=false).',
            )

        purchase_qty = _parse_decimal(
            raw.get('quantity'), f'lines[{index}].quantity',
        )
        idempotency_key = raw.get('idempotency_key')
        if idempotency_key in (None, ''):
            raise ReceiveError(f'lines[{index}].idempotency_key is required.')
        idempotency_key = str(idempotency_key)

        label_format, label_count = _resolve_label_plan(line, raw, index)
        unit_keys = _unit_idempotency_keys(idempotency_key, label_count)
        prior_entry = StockEntry.objects.filter(
            idempotency_key=unit_keys[0],
        ).first()
        queue_stock = _wants_queued_stock(raw, label_format=label_format)
        queued = queued_hold_for_line(line)
        open_qty = (line.qty_balance - queued).quantize(Decimal('0.000001'))
        leftover = Decimal('0')
        shortfall_reason = None
        if prior_entry is None:
            if line.line_closed or open_qty <= 0:
                raise ReceiveError(f'Line {line.line_no} has no open balance.')
            if purchase_qty > open_qty:
                raise ReceiveError(
                    f'Line {line.line_no}: quantity {purchase_qty} exceeds '
                    f'balance {open_qty}.',
                )
            leftover = (open_qty - purchase_qty).quantize(Decimal('0.000001'))
            shortfall_reason = _parse_shortfall(raw, index, leftover)

        lot_body = raw.get('lot') if isinstance(raw.get('lot'), dict) else {}
        use_by = _optional_date(
            lot_body.get('use_by') or dline.use_by or line.use_by,
            f'lines[{index}].lot.use_by',
        )
        production_date = _optional_date(
            lot_body.get('production_date') or dline.production_date or line.production_date,
            f'lines[{index}].lot.production_date',
        )
        trace_number = (
            lot_body.get('trace_number')
            or dline.trace_number
            or line.trace_number
            or delivery.delivery_trace_number
        )

        try:
            lot = stock_services.resolve_lot(
                product_id=line.product_id,
                trace_number=trace_number,
                use_by=use_by,
                production_date=production_date,
                origin=StockLotOrigin.PURCHASE,
                shape_format_id=(
                    line.product_supplier.purchase_shape_format_id
                    if line.product_supplier_id
                    else None
                ),
                supplier_lot_code=lot_body.get('supplier_lot_code'),
            )
        except StockValidationError as exc:
            raise ReceiveError(str(exc)) from exc

        unit_cost = line.unit_cost
        if raw.get('unit_cost') not in (None, ''):
            unit_cost = _parse_decimal(
                raw.get('unit_cost'), f'lines[{index}].unit_cost',
            )

        mapping = line.product_supplier if line.product_supplier_id else None
        if mapping is not None:
            receipt_qty = purchase_qty
            product_supplier = mapping
            unit_id = None
        else:
            receipt_qty = _stock_quantity(line, purchase_qty)
            product_supplier = None
            unit_id = line.product.unit_id

        receipt_audit = {
            'actor_user_id': (
                audit.get('actor_user_id')
                or body.get('actor_user_id')
                or body.get('checked_by_user_id')
            ),
            'lan_username': audit.get('lan_username') or body.get('lan_username'),
            'source_workstation': audit.get('source_workstation'),
            'source_workstation_ip': audit.get('source_workstation_ip'),
            'remarks': raw.get('remarks') or body.get('remarks') or audit.get('remarks'),
            'override_reason': audit.get('override_reason'),
            'authorised_by_user_id': audit.get('authorised_by_user_id'),
        }
        receipt_audit = {k: v for k, v in receipt_audit.items() if v is not None}
        qty_parts = _split_quantities(receipt_qty, label_count)
        purchase_parts = _split_quantities(purchase_qty, label_count)

        transactions = []
        last_entry = None
        for unit_index, (unit_key, part_qty, purchase_part) in enumerate(
            zip(unit_keys, qty_parts, purchase_parts), start=1,
        ):
            try:
                entry = stock_services.receipt(
                    idempotency_key=unit_key,
                    lot=lot,
                    location_id=location_id,
                    quantity=part_qty,
                    unit_id=unit_id,
                    effective_at=body.get('effective_at') or timezone.now(),
                    unit_cost=unit_cost,
                    product_supplier=product_supplier,
                    counterparty_location_id=po.supplier_id,
                    source_document_type='po',
                    source_document_id=po.id,
                    source_document_line=line.line_no,
                    po_number=po.external_number or po.number,
                    defer_balance=queue_stock,
                    **receipt_audit,
                )
            except StockValidationError as exc:
                raise ReceiveError(str(exc)) from exc

            last_entry = entry
            posting = None
            if queue_stock:
                posting = entry_posting.queue_entry(
                    entry=entry,
                    actor_user_id=receipt_audit.get('actor_user_id'),
                    lan_username=receipt_audit.get('lan_username'),
                    source_workstation=receipt_audit.get('source_workstation'),
                    meta={
                        'po_id': po.id,
                        'line_id': line.id,
                        'line_no': line.line_no,
                        'purchase_qty': str(purchase_part),
                    },
                )
            else:
                posting = entry_posting.get_posting(entry)

            tx = {
                'unit_index': unit_index,
                'stock_entry_id': entry.id,
                'entry_code': entry_labels.entry_code(entry.id),
                'quantity_stock': _qty_str(entry.quantity),
                'lot_id': lot.id,
                'idempotency_key': unit_key,
                'entry': _entry_payload(entry),
            }
            if posting is not None:
                tx['posting'] = entry_posting.posting_dict(posting)
                tx['posting_status'] = posting.status
            if label_format is not None:
                try:
                    label = entry_labels.create_entry_label(
                        entry=entry,
                        label_format=label_format,
                        label_count=1,
                        actor_user_id=receipt_audit.get('actor_user_id'),
                        lan_username=receipt_audit.get('lan_username'),
                        source_workstation=receipt_audit.get('source_workstation'),
                    )
                except StockValidationError as exc:
                    raise ReceiveError(
                        f'lines[{index}]: {exc}',
                    ) from exc
                tx['label'] = entry_labels.label_state_dict(label)
                tx['goods_in_label'] = entry_labels.build_goods_in_label(
                    entry, label,
                )
            # Optional unit serials only on first physical unit when requested.
            if unit_index == 1:
                units_payload = _print_units_for_line(
                    raw=raw,
                    index=index,
                    entry=entry,
                    idempotency_key=unit_key,
                    audit=receipt_audit,
                )
                if units_payload:
                    tx['units'] = units_payload
            transactions.append(tx)

        # Reused key: return existing stock, do not advance PO qty again.
        if prior_entry is None and last_entry is not None:
            before_qty = {
                'delivery_id': delivery.id,
                'line_id': line.id,
                'line_no': line.line_no,
                'qty_received': _qty_str(line.qty_received),
                'qty_rejected': _qty_str(line.qty_rejected),
                'qty_balance': _qty_str(line.qty_balance),
            }
            reject_qty = (
                leftover
                if shortfall_reason and shortfall_reason not in SHORTFALL_AWAIT_REASONS
                else Decimal('0')
            )
            if not queue_stock:
                line.qty_received = (line.qty_received + purchase_qty).quantize(
                    Decimal('0.000001'),
                )
            line.qty_rejected = (line.qty_rejected + reject_qty).quantize(
                Decimal('0.000001'),
            )
            line.qty_balance = (
                line.qty_ordered - line.qty_received - line.qty_rejected
            ).quantize(Decimal('0.000001'))
            if line.qty_balance < 0:
                raise ReceiveError(f'Line {line.line_no} balance went negative.')
            line.last_receipt_entry_id = last_entry.id
            if shortfall_reason:
                line.shortfall_reason = shortfall_reason
            if leftover > 0 and raw.get('remarks') not in (None, ''):
                line.remarks = str(raw['remarks'])[:256]
            if line.qty_balance == 0:
                line.line_closed = True
                line.stock_in_done = True
            line.save(
                update_fields=[
                    'qty_received',
                    'qty_rejected',
                    'qty_balance',
                    'shortfall_reason',
                    'remarks',
                    'line_closed',
                    'stock_in_done',
                    'last_receipt_entry_id',
                    'updated_at',
                ],
            )
            dline.qty_received = (dline.qty_received + purchase_qty).quantize(
                Decimal('0.000001'),
            )
            dline.qty_rejected = (dline.qty_rejected + reject_qty).quantize(
                Decimal('0.000001'),
            )
            dline.last_receipt_entry_id = last_entry.id
            dline.save(
                update_fields=[
                    'qty_received',
                    'qty_rejected',
                    'last_receipt_entry_id',
                    'updated_at',
                ],
            )
            did_receive = True
            if leftover > 0 and shortfall_reason:
                needs_credit = shortfall_reason not in SHORTFALL_AWAIT_REASONS
                record_history(
                    po=po,
                    delivery=delivery,
                    event_type=(
                        PurchaseOrderHistoryEvent.NON_CONFORMANCE
                        if needs_credit
                        else PurchaseOrderHistoryEvent.NOTE
                    ),
                    remarks=(
                        f'Line {line.line_no} rejected {reject_qty}: '
                        f'{shortfall_reason} - credit note'
                        if needs_credit
                        else (
                            f'Line {line.line_no} short {leftover}: '
                            f'rest coming later'
                        )
                    ),
                    before=before_qty,
                    after={
                        'delivery_id': delivery.id,
                        'line_id': line.id,
                        'line_no': line.line_no,
                        'qty_received': str(purchase_qty),
                        'qty_short': str(leftover),
                        'qty_rejected': str(reject_qty),
                        'shortfall_reason': shortfall_reason,
                        'needs_credit_note': needs_credit,
                        'remarks': raw.get('remarks'),
                    },
                    actor=audit.get('actor') or actor_json(audit=receipt_audit),
                )

        first = transactions[0]
        row = {
            'line_id': line.id,
            'line_no': line.line_no,
            'quantity_ordered_units': _qty_str(purchase_qty),
            'quantity_stock': _qty_str(
                sum((Decimal(t['quantity_stock']) for t in transactions), Decimal('0')),
            ),
            'qty_received': _qty_str(line.qty_received),
            'qty_rejected': _qty_str(line.qty_rejected),
            'qty_queued': _qty_str(queued_hold_for_line(line)),
            'qty_balance': _qty_str(line.qty_balance),
            'shortfall_reason': line.shortfall_reason,
            'needs_credit_note': line.qty_rejected > 0,
            'stock_entry_id': first['stock_entry_id'],
            'entry_code': first['entry_code'],
            'lot_id': lot.id,
            'stock_in_done': line.stock_in_done,
            'location_id': location_id,
            'quarantine': quarantine,
            'idempotent_replay': prior_entry is not None,
            'label_format': label_format,
            'label_count': label_count,
            'transaction_count': len(transactions),
            'transactions': transactions,
            'entry': first['entry'],
        }
        if first.get('units'):
            row['units'] = first['units']
        if first.get('posting') is not None:
            row['posting'] = first['posting']
            row['posting_status'] = first['posting_status']
        if first.get('goods_in_label') is not None:
            row['label'] = first['label']
            row['goods_in_label'] = first['goods_in_label']
        results.append(row)

    recompute_po_status(po)
    if did_receive:
        delivery.status = PurchaseOrderDeliveryStatus.RECEIVED
        delivery.save(update_fields=['status', 'updated_at'])
        sync_po_from_delivery(delivery)
    form = resolve_goods_in_form(po.id, delivery_id=delivery.id)
    form['receive_results'] = results
    return form
