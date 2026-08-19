from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from stock_ledger.models import StockBalance, StockEntry, StockFifoOverride, StockLotOrigin, StockUnit
from stock_ledger.util import entry_labels, entry_posting, services, stickers, stock_units
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.parse import (
    optional_unit_id as _optional_unit_id,
    parse_decimal as _parse_decimal,
    parse_effective_at as _parse_effective_at,
    parse_requirement_ids as _parse_requirement_ids,
    parse_unit_moves as _parse_unit_moves,
)
from stock_ledger.util.payloads import entry_dict, stock_unit_dict
from stock_ledger.views._common import (
    _common_write_kwargs,
    _fifo_batch_rows,
    _parse_json_body,
    _receipt_product_supplier,
    _receipt_supplier_location_id,
    _resolve_lot,
    _resolve_source_entry,
)
from users_rbac.permissions import gate_floor_write, gate_warehouse_write


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def receipt_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        if (
            body.get('po_number') not in (None, '')
            or str(body.get('source_document_type') or '').lower() == 'po'
        ):
            return api_error(
                'PO goods-in must use POST /purchasing/pos/<po_id>/receive/. '
                'Do not pass po_number or source_document_type=po to /stock/receipt/.',
            )
        audit = _common_write_kwargs(request, body)
        lot = _resolve_lot(body)
        mapping = _receipt_product_supplier(body, lot)
        supplier_location_id = (
            mapping.supplier_id if mapping is not None
            else _receipt_supplier_location_id(body, lot)
        )
        if (
            lot.origin == StockLotOrigin.PURCHASE
            and supplier_location_id is None
        ):
            raise StockValidationError(
                'supplier_id or product_supplier_id is required for purchase goods-in.',
            )
        queue_stock = body.get('queue_stock') in (True, 'true', '1', 'yes', 'True')
        label_format = body.get('label_format')
        label_count = 1
        if label_format not in (None, ''):
            label_format = str(label_format).strip().lower()
            if label_format not in ('pallet', 'box'):
                raise StockValidationError('label_format must be pallet or box.')
            if body.get('label_count') in (None, ''):
                label_count = 1 if label_format == 'pallet' else 0
            else:
                label_count = int(body['label_count'])
            if label_count < 1:
                raise StockValidationError(
                    'label_count is required when label_format is set.',
                )
            if label_format == 'pallet' and label_count != 1:
                raise StockValidationError(
                    'label_format=pallet requires label_count=1.',
                )
            if body.get('queue_stock') not in (
                False, 'false', '0', 'no', 'False',
            ):
                queue_stock = True

        total_qty = _parse_decimal(body['quantity'], 'quantity')
        if label_count == 1:
            qty_parts = [total_qty]
            unit_keys = [body['idempotency_key']]
        else:
            base = (total_qty / label_count).quantize(Decimal('0.000001'))
            qty_parts = [base] * (label_count - 1)
            qty_parts.append(
                (total_qty - sum(qty_parts)).quantize(Decimal('0.000001')),
            )
            if qty_parts[-1] <= 0:
                raise StockValidationError(
                    f'Cannot split quantity {total_qty} into {label_count}.',
                )
            unit_keys = [
                f"{body['idempotency_key']}:u:{i}"
                for i in range(1, label_count + 1)
            ]

        transactions = []
        data = None
        for unit_index, (unit_key, part_qty) in enumerate(
            zip(unit_keys, qty_parts), start=1,
        ):
            entry = services.receipt(
                idempotency_key=unit_key,
                lot=lot,
                location_id=int(body['location_id']),
                quantity=part_qty,
                unit_id=_optional_unit_id(body),
                effective_at=_parse_effective_at(body.get('effective_at')),
                unit_cost=(
                    _parse_decimal(body['unit_cost'], 'unit_cost')
                    if body.get('unit_cost') not in (None, '')
                    else None
                ),
                product_supplier=mapping,
                counterparty_location_id=supplier_location_id,
                defer_balance=queue_stock,
                **audit,
            )
            if supplier_location_id is not None and entry.counterparty_location_id:
                entry = (
                    StockEntry.objects
                    .select_related(
                        'counterparty_location',
                        'location',
                        'unit',
                        'lot__product',
                    )
                    .get(pk=entry.pk)
                )
            tx = entry_dict(entry)
            tx['unit_index'] = unit_index
            tx['entry_code'] = entry_labels.entry_code(entry.id)
            if queue_stock:
                posting = entry_posting.queue_entry(
                    entry=entry,
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['posting'] = entry_posting.posting_dict(posting)
                tx['posting_status'] = posting.status
            if label_format not in (None, ''):
                label = entry_labels.create_entry_label(
                    entry=entry,
                    label_format=label_format,
                    label_count=1,
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['label'] = entry_labels.label_state_dict(label)
                tx['goods_in_label'] = entry_labels.build_goods_in_label(
                    entry, label,
                )
            if unit_index == 1:
                print_count = body.get('print_unit_count')
                print_qty = body.get('print_quantity_per_unit')
                if print_count not in (None, '') or print_qty not in (None, ''):
                    if print_count in (None, '') or print_qty in (None, ''):
                        return api_error(
                            'print_unit_count and print_quantity_per_unit are both required to print labels.',
                        )
                    units = stock_units.create_units_for_entry(
                        source_entry=entry,
                        unit_count=int(print_count),
                        quantity_per_unit=_parse_decimal(
                            print_qty, 'print_quantity_per_unit',
                        ),
                        idempotency_key_prefix=(
                            str(body['print_idempotency_key_prefix'])
                            if body.get('print_idempotency_key_prefix') not in (None, '')
                            else f"{unit_key}:print"
                        ),
                        actor_user_id=audit.get('actor_user_id'),
                        lan_username=audit.get('lan_username'),
                        source_workstation=audit.get('source_workstation'),
                    )
                    tx['units'] = [stock_unit_dict(u) for u in units]
                data = dict(tx)
            transactions.append(tx)

        data['label_format'] = label_format
        data['label_count'] = label_count
        data['transaction_count'] = len(transactions)
        data['transactions'] = transactions
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Receipt posted.', data, status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_out')
def issue_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source_entry = _resolve_source_entry(body)
        if source_entry is not None:
            lot = source_entry.lot
            location_id = (
                int(body['location_id'])
                if body.get('location_id') not in (None, '')
                else source_entry.location_id
            )
        else:
            lot = _resolve_lot(body)
            location_id = int(body['location_id'])
        entry = services.issue(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=location_id,
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
        data = entry_dict(entry)
        if source_entry is not None:
            data['source_entry_id'] = source_entry.id
            data['source_entry_code'] = entry_labels.entry_code(source_entry.id)
        copies = body.get('goods_out_label_count')
        if copies not in (None, ''):
            data['goods_out_label'] = entry_labels.build_goods_out_label(
                issue_entry=entry,
                source_entry=source_entry,
                copies=int(copies),
            )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Issue posted.', data, status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def transfer_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    unit_moves = None
    try:
        unit_moves = _parse_unit_moves(body)
        queue_stock = body.get('queue_stock') in (True, 'true', '1', 'yes', 'True')
        audit = _common_write_kwargs(request, body)
        req_ids = _parse_requirement_ids(body.get('requirement_ids'))
        if req_ids:
            audit['source_document_type'] = 'plan_requirement'
            audit['source_document_id'] = req_ids[0]
        lot = _resolve_lot(body)
        from_location_id = int(body['from_location_id'])
        quantity = _parse_decimal(body['quantity'], 'quantity')
        source_entry = _resolve_source_entry(body)
        if source_entry is not None:
            balance = (
                StockBalance.objects
                .filter(lot_id=lot.id, location_id=from_location_id)
                .only('quantity')
                .first()
            )
            stickers.check_draw(
                source_entry=source_entry,
                lot=lot,
                location_id=from_location_id,
                quantity=quantity,
                lot_quantity=balance.quantity if balance is not None else None,
            )
        fifo_reason = str(body.get('fifo_override_reason') or '').strip()
        recommended_lot_id = None
        if queue_stock:
            batches = _fifo_batch_rows(
                product_id=lot.product_id,
                location_id=from_location_id,
            )
            recommended_lot_id = batches[0]['lot_id'] if batches else None
            if (
                recommended_lot_id is not None
                and recommended_lot_id != lot.id
                and not fifo_reason
            ):
                raise StockValidationError(
                    'fifo_override_reason is required when not using oldest stock.',
                )
        out_entry, in_entry = services.transfer(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            from_location_id=from_location_id,
            to_location_id=int(body['to_location_id']),
            quantity=quantity,
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            unit_moves=unit_moves,
            defer_balance=queue_stock,
            source_entry=source_entry,
            **audit,
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))

    payload = {'out': entry_dict(out_entry), 'in': entry_dict(in_entry)}
    if queue_stock:
        out_entry = (
            StockEntry.objects
            .select_related('lot__product', 'unit')
            .get(pk=out_entry.pk)
        )
        posting = entry_posting.queue_entry(
            entry=out_entry,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
        label = entry_labels.create_entry_label(
            entry=out_entry,
            label_format='box',
            label_count=1,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
        payload['posting'] = entry_posting.posting_dict(posting)
        payload['label'] = entry_labels.label_state_dict(label)
        payload['goods_out_label'] = entry_labels.build_goods_out_label(
            issue_entry=out_entry,
        )
        if (
            recommended_lot_id is not None
            and recommended_lot_id != lot.id
            and fifo_reason
        ):
            StockFifoOverride.objects.get_or_create(
                stock_entry=out_entry,
                defaults={
                    'product_id': lot.product_id,
                    'scanned_lot_id': lot.id,
                    'recommended_lot_id': recommended_lot_id,
                    'reason': fifo_reason,
                    'actor_user_id': audit.get('actor_user_id'),
                    'lan_username': (audit.get('lan_username') or None),
                },
            )
    if unit_moves is not None:
        serials = []
        for row in unit_moves:
            if isinstance(row, dict) and row.get('unit_serial'):
                serials.append(
                    stock_units.resolve_unit_serial(str(row['unit_serial'])),
                )
        units = (
            StockUnit.objects
            .filter(unit_serial__in=serials)
            .select_related('lot__product', 'unit', 'location')
            .order_by('id')
        )
        payload['units'] = [stock_unit_dict(u) for u in units]
    return api_success(
        'Transfer queued.' if queue_stock else 'Transfer posted.',
        payload,
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_out')
def disposal_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        entry = services.disposal(
            idempotency_key=body['idempotency_key'],
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Disposal posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def count_adjustment_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        counted = body.get('counted_quantity')
        delta = body.get('quantity_delta')
        entry = services.count_adjustment(
            idempotency_key=body['idempotency_key'],
            lot=_resolve_lot(body),
            location_id=int(body['location_id']),
            counted_quantity=(
                _parse_decimal(counted, 'counted_quantity')
                if counted not in (None, '')
                else None
            ),
            quantity_delta=(
                _parse_decimal(delta, 'quantity_delta')
                if delta not in (None, '')
                else None
            ),
            unit_id=_optional_unit_id(body),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Count adjustment posted.', entry_dict(entry), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
@gate_floor_write
def reversal_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source = StockEntry.objects.get(pk=body['entry_id'])
        entry = services.reversal(
            idempotency_key=body['idempotency_key'],
            entry=source,
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except StockEntry.DoesNotExist:
        return api_error('entry_id not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success('Reversal posted.', entry_dict(entry), status_code=201)
