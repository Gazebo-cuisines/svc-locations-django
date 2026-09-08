from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from hardware.services import touch_from_request
from locations.utils.api_response import api_error, api_success
from product.models import Product, ProductSupplier
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryType,
    StockFifoOverride,
    StockLot,
    StockLotOrigin,
    StockUnit,
)
from stock_ledger.util import entry_labels, entry_posting, services, stickers, stock_units
from stock_ledger.util.conversions import StockValidationError, stock_to_kg
from stock_ledger.util.goods_out_form import (
    GoodsOutFormError,
    resolve_adhoc_goods_out_form,
)
from stock_ledger.util.serialize import entry_dict, stock_unit_dict, supplier_pack_fields
from stock_ledger.views.common import (
    _common_write_kwargs,
    _dec,
    _fifo_batch_rows,
    _optional_unit_id,
    _parse_decimal,
    _parse_effective_at,
    _parse_json_body,
    _product_destination_fields,
    _resolve_lot,
)
from users_rbac.permissions import gate_floor_write, gate_warehouse_write


def _receipt_product_supplier(body: dict, lot: StockLot):
    """Resolve optional product_supplier mapping for goods-in shape/packs."""
    ps_id = body.get('product_supplier_id')
    if ps_id in (None, ''):
        return None
    try:
        row = (
            ProductSupplier.objects
            .select_related('outer_unit', 'inner_unit', 'purchase_shape_format')
            .get(pk=int(ps_id), is_active=True)
        )
    except (ProductSupplier.DoesNotExist, TypeError, ValueError) as exc:
        raise StockValidationError(
            f'product_supplier_id={ps_id} not found or inactive',
        ) from exc
    if row.product_id != lot.product_id:
        raise StockValidationError(
            f'product_supplier_id={ps_id} is for product_id={row.product_id}, '
            f'lot is product_id={lot.product_id}',
        )
    return row


def _receipt_supplier_location_id(body: dict, lot: StockLot) -> int | None:
    """Supplier comes from product_supplier (shape-format row) when given."""
    row = _receipt_product_supplier(body, lot)
    if row is not None:
        return row.supplier_id

    for key in ('counterparty_location_id', 'supplier_id'):
        raw = body.get(key)
        if raw not in (None, ''):
            return int(raw)
    return None

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
            # pallet: label_count = print copies of one barcode (not N receipts).
            if body.get('queue_stock') not in (
                False, 'false', '0', 'no', 'False',
            ):
                queue_stock = True

        total_qty = _parse_decimal(body['quantity'], 'quantity')
        split_parts = (
            1 if label_format == 'pallet' else label_count
        ) if label_format not in (None, '') else 1
        if split_parts == 1:
            qty_parts = [total_qty]
            unit_keys = [body['idempotency_key']]
        else:
            base = (total_qty / split_parts).quantize(Decimal('0.000001'))
            qty_parts = [base] * (split_parts - 1)
            qty_parts.append(
                (total_qty - sum(qty_parts)).quantize(Decimal('0.000001')),
            )
            if qty_parts[-1] <= 0:
                raise StockValidationError(
                    f'Cannot split quantity {total_qty} into {split_parts}.',
                )
            unit_keys = [
                f"{body['idempotency_key']}:u:{i}"
                for i in range(1, split_parts + 1)
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
                    label_count=(
                        label_count if label_format == 'pallet' else 1
                    ),
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

def _resolve_source_entry(body: dict) -> StockEntry | None:
    """Goods-in sticker (E{id}) that a goods-out row draws its stock from."""
    raw = body.get('source_entry_id')
    if raw in (None, '') and body.get('source_entry_code') not in (None, ''):
        raw = entry_labels.parse_entry_code(str(body['source_entry_code']))
        if raw is None:
            raise StockValidationError('source_entry_code must look like E123.')
    if raw in (None, ''):
        return None
    entry = entry_labels.get_entry_for_label(int(raw))
    entry_labels.require_receipt_entry(entry)
    return entry


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

def _parse_unit_moves(body: dict) -> list | None:
    raw = body.get('unit_moves')
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise StockValidationError('unit_moves must be a list')
    return raw


def _parse_requirement_ids(raw) -> list[int] | None:
    if raw in (None, ''):
        return None
    if not isinstance(raw, list):
        raise StockValidationError('requirement_ids must be a list.')
    if not raw:
        return None
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError) as exc:
        raise StockValidationError('requirement_ids must be integers.') from exc

def _resolve_transfer_to_location_id(body: dict, lot: StockLot) -> int:
    """
    With plan / explicit body: to_location_id required.
    Without plan: omit to_location_id → product.destination_container.
    """
    raw = body.get('to_location_id')
    if raw not in (None, ''):
        return int(raw)
    dest_id = lot.product.destination_container_id
    if dest_id is None:
        raise StockValidationError(
            'This product has no destination location. '
            'Set destination_container on the product before Goods Out without plan.',
        )
    return int(dest_id)


def _transfer_label_plan(body: dict) -> tuple[str | None, int]:
    """Optional label_format/label_count for queued Goods OUT (mirror receipt)."""
    label_format = body.get('label_format')
    if label_format in (None, ''):
        return None, 1
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
    # pallet: label_count = print copies (transfer still one OUT row).
    return label_format, label_count


def _split_label_quantities(total: Decimal, parts: int) -> list[Decimal]:
    if parts < 1:
        raise StockValidationError('label_count must be >= 1.')
    if parts == 1:
        return [total]
    base = (total / parts).quantize(Decimal('0.000001'))
    qty_parts = [base] * (parts - 1)
    last = (total - sum(qty_parts)).quantize(Decimal('0.000001'))
    if last <= 0:
        raise StockValidationError(
            f'Cannot split quantity {total} into {parts}.',
        )
    qty_parts.append(last)
    return qty_parts


def _parse_transfer_lines(body: dict) -> list[dict] | None:
    """Multi-GI cart: [{lot_id, quantity, source_entry_id}, …]."""
    raw = body.get('lines')
    if raw in (None, ''):
        return None
    if not isinstance(raw, list) or not raw:
        raise StockValidationError('lines must be a non-empty array.')
    lines = []
    seen_entries: set[int] = set()
    for i, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            raise StockValidationError(f'lines[{i}] must be an object.')
        lot = _resolve_lot(row)
        qty = _parse_decimal(row.get('quantity'), f'lines[{i}].quantity')
        if qty <= 0:
            raise StockValidationError(f'lines[{i}].quantity must be > 0.')
        source_entry = _resolve_source_entry(row)
        if source_entry is None:
            raise StockValidationError(
                f'lines[{i}].source_entry_id is required.',
            )
        if source_entry.id in seen_entries:
            raise StockValidationError(
                f'Duplicate source_entry_id {source_entry.id} in lines.',
            )
        seen_entries.add(source_entry.id)
        lines.append({
            'lot': lot,
            'quantity': qty,
            'source_entry': source_entry,
        })
    product_ids = {line['lot'].product_id for line in lines}
    if len(product_ids) != 1:
        raise StockValidationError(
            'All lines must be for the same product.',
        )
    return lines


def _fifo_override_needed_for_work(
    *,
    product_id: int,
    location_id: int,
    work: list[dict],
) -> tuple[bool, int | None]:
    """Simulate draws in order; True if any line skips current oldest lot."""
    batches = _fifo_batch_rows(product_id=product_id, location_id=location_id)
    if not batches:
        return False, None
    remaining = {
        row['lot_id']: Decimal(str(row['quantity'])) for row in batches
    }
    recommended = batches[0]['lot_id']
    needs = False
    for item in work:
        lot_id = item['lot'].id
        oldest = next(
            (
                row['lot_id']
                for row in batches
                if remaining.get(row['lot_id'], Decimal('0')) > 0
            ),
            None,
        )
        if oldest is not None and lot_id != oldest:
            needs = True
        remaining[lot_id] = remaining.get(lot_id, Decimal('0')) - item['quantity']
    return needs, recommended


def _suggest_goods_out_picks(
    *,
    product: Product,
    location_id: int,
    required: Decimal,
) -> dict:
    """FIFO GI stickers covering required qty (full boxes; may overshoot)."""
    if required <= 0:
        raise StockValidationError('quantity must be > 0.')
    batches = _fifo_batch_rows(product_id=product.id, location_id=location_id)
    need = required
    picks = []
    suggested = Decimal('0')
    for rank, row in enumerate(batches):
        if need <= 0:
            break
        lot_id = row['lot_id']
        lot_left = Decimal(str(row['quantity']))
        if lot_left <= 0:
            continue
        receipts = list(
            StockEntry.objects
            .filter(
                entry_type=StockEntryType.RECEIPT,
                lot_id=lot_id,
                location_id=location_id,
                reversed_by__isnull=True,
            )
            .select_related(
                'lot__product',
                'lot__product_supplier__outer_unit',
                'unit',
            )
            .order_by('id')
        )
        for receipt in receipts:
            if need <= 0 or lot_left <= 0:
                break
            rem = stickers.remaining_for_entry(receipt, lot_quantity=lot_left)
            if rem <= 0:
                continue
            lot = receipt.lot
            pack = supplier_pack_fields(
                rem, product, getattr(lot, 'product_supplier', None),
            )
            picks.append({
                'entry_id': receipt.id,
                'entry_code': entry_labels.entry_code(receipt.id),
                'lot_id': lot.id,
                'trace_number': lot.trace_number,
                'use_by': lot.use_by.isoformat() if lot.use_by else None,
                'quantity': _dec(rem),
                'fifo_rank': rank,
                **pack,
            })
            suggested += rem
            lot_left -= rem
            need -= rem
    shortfall = need if need > 0 else Decimal('0')
    dest = _product_destination_fields(product)
    return {
        'product_id': product.id,
        'product_name': product.name,
        'location_id': location_id,
        'required_quantity': _dec(required),
        'suggested_quantity': _dec(suggested),
        'shortfall': _dec(shortfall),
        'display_kg': _dec(stock_to_kg(suggested, product)),
        'pick_count': len(picks),
        'picks': picks,
        **{k: dest[k] for k in (
            'to_location_id', 'to_location_name', 'dest_ok',
            'destination_container_id', 'destination_container_name',
        )},
    }


@csrf_exempt
@require_GET
def goods_out_suggest_api(request):
    """Qty-first FIFO sticker list for Goods Out without plan."""
    product_id = request.GET.get('product_id')
    location_id = request.GET.get('location_id')
    quantity = request.GET.get('quantity')
    if product_id in (None, ''):
        return api_error('product_id is required.')
    if location_id in (None, ''):
        return api_error('location_id is required.')
    if quantity in (None, ''):
        return api_error('quantity is required.')
    try:
        pid = int(product_id)
        loc_id = int(location_id)
        required = _parse_decimal(quantity, 'quantity')
    except (TypeError, ValueError) as exc:
        return api_error(str(exc))
    product = (
        Product.objects
        .select_related('destination_container', 'unit')
        .filter(pk=pid)
        .first()
    )
    if product is None:
        return api_error(f'product_id={pid} not found.', status_code=404)
    try:
        data = _suggest_goods_out_picks(
            product=product,
            location_id=loc_id,
            required=required,
        )
    except StockValidationError as exc:
        return api_error(str(exc))
    if not data['dest_ok']:
        data['dest_message'] = (
            'This product has no destination location. '
            'Fix product master before Goods Out without plan.'
        )
    touch_from_request(request, action='scan', location_id=loc_id)
    return api_success('Goods Out picks suggested.', data)


@csrf_exempt
@require_GET
def goods_out_form_api(request):
    """Without-plan wizard: Find → Qty → FIFO → Scan → Queue → Print → Verify."""
    raw = request.GET.get('location_id')
    if raw in (None, ''):
        return api_error('location_id is required.')
    try:
        location_id = int(raw)
    except (TypeError, ValueError):
        return api_error('location_id must be an integer.')
    try:
        data = resolve_adhoc_goods_out_form(location_id)
    except GoodsOutFormError as exc:
        return api_error(str(exc), status_code=404)
    return api_success('Goods out form resolved successfully.', data)


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
        label_format, label_count = _transfer_label_plan(body)
        if label_format is not None and body.get('queue_stock') not in (
            False, 'false', '0', 'no', 'False',
        ):
            queue_stock = True
        lines = _parse_transfer_lines(body)
        split_labels = label_format == 'box' and label_count > 1
        if lines is not None and split_labels:
            raise StockValidationError(
                'lines cannot be combined with label_count > 1.',
            )
        if lines is not None and unit_moves is not None:
            raise StockValidationError(
                'unit_moves cannot be combined with lines.',
            )
        if split_labels and unit_moves is not None:
            raise StockValidationError(
                'unit_moves cannot be combined with label_count > 1.',
            )
        audit = _common_write_kwargs(request, body)
        req_ids = _parse_requirement_ids(body.get('requirement_ids'))
        auto_dest = body.get('to_location_id') in (None, '')
        if req_ids:
            audit['source_document_type'] = 'plan_requirement'
            audit['source_document_id'] = req_ids[0]
        elif auto_dest and not audit.get('source_document_type'):
            # Without-plan goods out — dest from product; never invent a plan line.
            audit['source_document_type'] = 'goods_out_adhoc'
        from_location_id = int(body['from_location_id'])
        fifo_reason = str(body.get('fifo_override_reason') or '').strip()
        effective_at = _parse_effective_at(body.get('effective_at'))
        unit_id = _optional_unit_id(body)
        out_fmt = label_format or 'box'
        required_quantity = None
        if body.get('required_quantity') not in (None, ''):
            required_quantity = _parse_decimal(
                body['required_quantity'], 'required_quantity',
            )

        if lines is not None:
            work = []
            for i, line in enumerate(lines, start=1):
                lot = line['lot']
                source_entry = line['source_entry']
                part_qty = line['quantity']
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
                    quantity=part_qty,
                    lot_quantity=(
                        balance.quantity if balance is not None else None
                    ),
                )
                work.append({
                    'key': (
                        body['idempotency_key']
                        if len(lines) == 1
                        else f"{body['idempotency_key']}:l:{i}"
                    ),
                    'lot': lot,
                    'quantity': part_qty,
                    'source_entry': source_entry,
                    'unit_moves': None,
                })
            if required_quantity is not None:
                picked = sum((item['quantity'] for item in work), Decimal('0'))
                if picked < required_quantity:
                    raise StockValidationError(
                        f'Picked {picked} is less than required '
                        f'{required_quantity}.',
                    )
            first_lot = work[0]['lot']
            to_location_id = _resolve_transfer_to_location_id(body, first_lot)
            needs_override, recommended_lot_id = _fifo_override_needed_for_work(
                product_id=first_lot.product_id,
                location_id=from_location_id,
                work=work,
            )
            if queue_stock and needs_override and not fifo_reason:
                raise StockValidationError(
                    'fifo_override_reason is required when not using oldest stock.',
                )
            response_label_count = len(work)
        else:
            lot = _resolve_lot(body)
            to_location_id = _resolve_transfer_to_location_id(body, lot)
            total_qty = _parse_decimal(body['quantity'], 'quantity')
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
                    quantity=total_qty,
                    lot_quantity=(
                        balance.quantity if balance is not None else None
                    ),
                )
            qty_parts = _split_label_quantities(
                total_qty,
                1 if label_format == 'pallet' else label_count,
            )
            split_n = len(qty_parts)
            if split_n == 1:
                unit_keys = [body['idempotency_key']]
            else:
                unit_keys = [
                    f"{body['idempotency_key']}:u:{i}"
                    for i in range(1, split_n + 1)
                ]
            work = [
                {
                    'key': unit_key,
                    'lot': lot,
                    'quantity': part_qty,
                    'source_entry': source_entry,
                    'unit_moves': unit_moves if split_n == 1 else None,
                }
                for unit_key, part_qty in zip(unit_keys, qty_parts)
            ]
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
                        'fifo_override_reason is required when not using '
                        'oldest stock.',
                    )
            response_label_count = label_count

        transactions = []
        for unit_index, item in enumerate(work, start=1):
            lot = item['lot']
            out_entry, in_entry = services.transfer(
                idempotency_key=item['key'],
                lot=lot,
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                quantity=item['quantity'],
                unit_id=unit_id,
                effective_at=effective_at,
                unit_moves=item['unit_moves'],
                defer_balance=queue_stock,
                source_entry=item['source_entry'],
                **audit,
            )
            tx = {
                'unit_index': unit_index,
                'out': entry_dict(out_entry),
                'in': entry_dict(in_entry),
            }
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
                    label_format=out_fmt,
                    label_count=(
                        label_count if out_fmt == 'pallet' else 1
                    ),
                    actor_user_id=audit.get('actor_user_id'),
                    lan_username=audit.get('lan_username'),
                    source_workstation=audit.get('source_workstation'),
                )
                tx['out'] = entry_dict(out_entry)
                tx['posting'] = entry_posting.posting_dict(posting)
                tx['label'] = entry_labels.label_state_dict(label)
                tx['goods_out_label'] = entry_labels.build_goods_out_label(
                    issue_entry=out_entry,
                    copies=label.label_count,
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
            transactions.append(tx)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))

    first = transactions[0]
    payload = {
        'out': first['out'],
        'in': first['in'],
        'label_format': out_fmt if queue_stock else label_format,
        'label_count': response_label_count,
        'transaction_count': len(transactions),
        'transactions': transactions,
    }
    if required_quantity is not None:
        payload['required_quantity'] = _dec(required_quantity)
    if queue_stock:
        payload['posting'] = first['posting']
        payload['label'] = first['label']
        payload['goods_out_label'] = first['goods_out_label']
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
        source_entry = _resolve_source_entry(body)
        lot = _resolve_lot(body) if source_entry is None else source_entry.lot
        location_id = (
            source_entry.location_id
            if source_entry is not None and body.get('location_id') in (None, '')
            else int(body['location_id'])
        )
        entry = services.count_adjustment(
            idempotency_key=body['idempotency_key'],
            lot=lot,
            location_id=location_id,
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
            source_entry=source_entry,
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
