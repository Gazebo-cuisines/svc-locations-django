import base64
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from hardware.services import serial_from_request, touch_from_request
from locations.models import Location
from product.models import Product
from stock_ledger.models import StockLot, StockLotOrigin
from stock_ledger.util import services
from stock_ledger.util.allocation_status import exclude_incomplete_lot_ids
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.fifo import fifo_balances
from stock_ledger.util.serialize import receipt_meta_by_lot_ids, serialize_balance_rows
from users_rbac.auth import attach_user, client_ip


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _dec(value):
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field_name}.') from exc


def _parse_effective_at(value):
    if value in (None, ''):
        return timezone.now()
    dt = parse_datetime(str(value))
    if dt is None:
        raise ValueError('Invalid effective_at. Use ISO-8601 datetime.')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _parse_date(value, field_name: str):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.') from exc


def _parse_clock(value, field_name: str):
    """HH:MM or HH:MM:SS → time; empty → None."""
    if value in (None, ''):
        return None
    text = str(value).strip()
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f'Invalid time for {field_name}. Use HH:MM.')


def _format_display_date(value) -> str | None:
    """YYYY-MM-DD / date → '03 Aug 2026' for UI lists."""
    if value in (None, ''):
        return None
    if isinstance(value, date):
        d = value
    else:
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return d.strftime('%d %b %Y')


def _optional_int_param(raw, field_name: str):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer.') from exc


def _require_lot(lot_id) -> StockLot:
    try:
        lot = StockLot.objects.select_related(
            'product',
            'product__destination_container',
        ).get(pk=lot_id)
    except StockLot.DoesNotExist as exc:
        raise StockValidationError(f'lot_id={lot_id} not found') from exc
    if not lot.product.is_active:
        raise StockValidationError(f'lot_id={lot_id} product is inactive')
    return lot


def _resolve_lot(body: dict) -> StockLot:
    """lot_id OR product_id + soft attrs (use_by / trace / …)."""
    lot_id = body.get('lot_id')
    if lot_id not in (None, ''):
        return _require_lot(lot_id)

    product_id = body.get('product_id')
    if product_id in (None, ''):
        raise StockValidationError('lot_id or product_id is required')

    lot = services.resolve_lot(
        product_id=int(product_id),
        trace_number=body.get('trace_number') or None,
        use_by=_parse_date(body.get('use_by'), 'use_by'),
        production_date=_parse_date(body.get('production_date'), 'production_date'),
        recipe_version_id=body.get('recipe_version_id') or None,
        shape_format_id=body.get('shape_format_id') or None,
        product_supplier_id=body.get('product_supplier_id') or None,
        origin=body.get('origin') or StockLotOrigin.PURCHASE,
        supplier_lot_code=body.get('supplier_lot_code') or None,
    )
    return StockLot.objects.select_related('product').get(pk=lot.pk)


def _optional_unit_id(body: dict) -> int | None:
    raw = body.get('unit_id')
    if raw in (None, ''):
        return None
    return int(raw)

def _product_destination_fields(product: Product) -> dict:
    """Auto dest for Goods Out without plan — product.destination_container."""
    dest = getattr(product, 'destination_container', None)
    dest_id = product.destination_container_id
    if dest is None and dest_id:
        dest = Location.objects.filter(pk=dest_id).only('id', 'name').first()
    return {
        'destination_container_id': dest_id,
        'destination_container_name': dest.name if dest is not None else None,
        'to_location_id': dest_id,
        'to_location_name': dest.name if dest is not None else None,
        'dest_ok': dest_id is not None,
    }

def _decode_bearer_claims(request) -> dict:
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


def _common_write_kwargs(request, body: dict) -> dict:
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
        claims = _decode_bearer_claims(request)
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

def _fifo_batch_rows(
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
    serialized = serialize_balance_rows(rows, receipt_meta=receipt_meta)
    for rank, (balance, row) in enumerate(zip(rows, serialized)):
        lot = balance.lot
        row['supplier_lot_code'] = lot.supplier_lot_code
        row['origin'] = lot.origin
        row['days_left'] = (lot.use_by - today).days if lot.use_by else None
        row['fifo_rank'] = rank
        batches.append(row)
    return batches
