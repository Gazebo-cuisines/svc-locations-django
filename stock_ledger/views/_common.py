import base64
import json

from django.utils import timezone

from hardware.services import serial_from_request, touch_from_request
from locations.utils.api_response import api_error
from product.models import ProductSupplier
from stock_ledger.models import StockEntry, StockLot, StockLotOrigin
from stock_ledger.util import entry_labels, services
from stock_ledger.util.allocation_status import exclude_incomplete_lot_ids
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.fifo import fifo_balances
from stock_ledger.util.parse import parse_date
from stock_ledger.util.payloads import product_supplier_for_lot
from stock_ledger.util.serialize import receipt_meta_by_lot_ids, serialize_balance_row
from users_rbac.auth import attach_user, client_ip


def parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def require_lot(lot_id) -> StockLot:
    try:
        lot = StockLot.objects.select_related('product').get(pk=lot_id)
    except StockLot.DoesNotExist as exc:
        raise StockValidationError(f'lot_id={lot_id} not found') from exc
    if not lot.product.is_active:
        raise StockValidationError(f'lot_id={lot_id} product is inactive')
    return lot


def resolve_lot(body: dict) -> StockLot:
    """lot_id OR product_id + soft attrs (use_by / trace / …)."""
    lot_id = body.get('lot_id')
    if lot_id not in (None, ''):
        return require_lot(lot_id)

    product_id = body.get('product_id')
    if product_id in (None, ''):
        raise StockValidationError('lot_id or product_id is required')

    lot = services.resolve_lot(
        product_id=int(product_id),
        trace_number=body.get('trace_number') or None,
        use_by=parse_date(body.get('use_by'), 'use_by'),
        production_date=parse_date(body.get('production_date'), 'production_date'),
        recipe_version_id=body.get('recipe_version_id') or None,
        shape_format_id=body.get('shape_format_id') or None,
        product_supplier_id=body.get('product_supplier_id') or None,
        origin=body.get('origin') or StockLotOrigin.PURCHASE,
        supplier_lot_code=body.get('supplier_lot_code') or None,
    )
    return StockLot.objects.select_related('product').get(pk=lot.pk)


def receipt_product_supplier(body: dict, lot: StockLot):
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


def receipt_supplier_location_id(body: dict, lot: StockLot) -> int | None:
    """Supplier comes from product_supplier (shape-format row) when given."""
    row = receipt_product_supplier(body, lot)
    if row is not None:
        return row.supplier_id

    for key in ('counterparty_location_id', 'supplier_id'):
        raw = body.get(key)
        if raw not in (None, ''):
            return int(raw)
    return None


def decode_bearer_claims(request) -> dict:
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return {}
    token = auth_header.split(' ', 1)[1].strip()
    parts = token.split('.')
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = '=' * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode('utf-8')
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def common_write_kwargs(request, body: dict) -> dict:
    attach_user(request, missing='ok', invalid='ok')
    user = getattr(request, 'rbac_user', None)
    if user:
        lan_username = (user.display_name or user.username)[:64]
        source_workstation = (request.META.get('HTTP_USER_AGENT') or '')[:64] or None
        source_workstation_ip = (
            getattr(request, 'client_ip', None) or client_ip(request) or ''
        )[:45] or None
        actor_user_id = user.id
    else:
        claims = decode_bearer_claims(request)
        # Person-facing label (match product audit). Never fall back to Cognito sub.
        lan_username = body.get('lan_username') or (
            claims.get('name')
            or claims.get('email')
            or claims.get('cognito:username')
            or claims.get('username')
        )
        source_workstation = (
            body.get('source_workstation') or request.META.get('HTTP_USER_AGENT')
        )
        source_workstation_ip = (
            body.get('source_workstation_ip') or request.META.get('REMOTE_ADDR')
        )
        actor_user_id = body.get('actor_user_id')
        if lan_username is not None:
            lan_username = str(lan_username)[:64]
        if source_workstation is not None:
            source_workstation = str(source_workstation)[:64]
        if source_workstation_ip is not None:
            source_workstation_ip = str(source_workstation_ip)[:45]

    device_serial = serial_from_request(request, body)
    if device_serial:
        touch_from_request(
            request,
            action='heartbeat',
            body=body,
            user=user,
            record_event=False,
        )

    kwargs = {
        'override_reason': body.get('override_reason'),
        'authorised_by_user_id': body.get('authorised_by_user_id'),
        'actor_user_id': actor_user_id,
        'lan_username': lan_username,
        'source_workstation': source_workstation,
        'source_workstation_ip': source_workstation_ip,
        'device_serial': device_serial,
        'remarks': body.get('remarks'),
        'source_document_type': body.get('source_document_type'),
        'source_document_id': body.get('source_document_id'),
        'source_document_line': body.get('source_document_line'),
        'po_number': body.get('po_number'),
    }
    po_number = body.get('po_number')
    if po_number not in (None, ''):
        po_number = str(po_number).strip()
        kwargs['po_number'] = po_number
        if kwargs.get('source_document_type') in (None, ''):
            kwargs['source_document_type'] = 'po'
        if kwargs.get('source_document_id') in (None, '') and po_number.isdigit():
            kwargs['source_document_id'] = int(po_number)
    return {k: v for k, v in kwargs.items() if v is not None}


def resolve_source_entry(body: dict) -> StockEntry | None:
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


def fifo_batch_rows(
    *,
    product_id: int,
    location_id: int | None,
    include_incomplete: bool = False,
) -> list[dict]:
    rows = list(fifo_balances(product_id=product_id, location_id=location_id)[:200])
    held = exclude_incomplete_lot_ids(
        location_id=location_id,
        lot_ids={b.lot_id for b in rows},
        include_incomplete=include_incomplete,
    )
    if held:
        rows = [b for b in rows if b.lot_id not in held]
    receipt_meta = receipt_meta_by_lot_ids({b.lot_id for b in rows})
    today = timezone.localdate()
    batches = []
    for rank, balance in enumerate(rows):
        lot = balance.lot
        row = serialize_balance_row(
            balance, receipt_meta=receipt_meta.get(balance.lot_id),
        )
        row['supplier_lot_code'] = lot.supplier_lot_code
        row['origin'] = lot.origin
        row['days_left'] = (lot.use_by - today).days if lot.use_by else None
        row['fifo_rank'] = rank
        batches.append(row)
    return batches


def flag(request, name: str) -> bool:
    return str(request.GET.get(name, '')).lower() in ('1', 'true', 'yes')


def fifo_check_error(match, loc_id, batches, code):
    if loc_id is None:
        return api_error('location_id is required when check_fifo=1.')
    lot = match.get('lot')
    if lot is None:
        return api_error(
            'Scan the goods-in or batch label, not the product barcode.',
        )
    scanned = (code or '').strip()
    if not batches:
        return api_error(
            'No stock for this item at this location.',
            data={'error': 'no_stock', 'scanned_code': scanned},
            status_code=409,
        )
    recommended = batches[0]
    if lot.id == recommended['lot_id']:
        return None
    rec_trace = recommended.get('trace_number')
    rec_use_by = recommended.get('use_by')
    use_by_bit = f' (use by {rec_use_by})' if rec_use_by else ''
    return api_error(
        f'Please use older stock first. Scan trace {rec_trace}{use_by_bit}, '
        f'or override.',
        data={
            'error': 'fifo_mismatch',
            'scanned_lot_id': lot.id,
            'scanned_trace': lot.trace_number,
            'scanned_code': scanned,
            'recommended_lot_id': recommended['lot_id'],
            'recommended_trace': rec_trace,
            'recommended_use_by': rec_use_by,
        },
        status_code=409,
    )


# Compatibility aliases used by view modules copied from views.py
_parse_json_body = parse_json_body
_require_lot = require_lot
_resolve_lot = resolve_lot
_receipt_product_supplier = receipt_product_supplier
_receipt_supplier_location_id = receipt_supplier_location_id
_decode_bearer_claims = decode_bearer_claims
_common_write_kwargs = common_write_kwargs
_resolve_source_entry = resolve_source_entry
_fifo_batch_rows = fifo_batch_rows
_flag = flag
_fifo_check_error = fifo_check_error
_product_supplier_for_lot = product_supplier_for_lot
