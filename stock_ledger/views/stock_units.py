from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Product
from stock_ledger.models import StockBalance, StockEntry, StockLot
from stock_ledger.util import stock_units
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.parse import parse_decimal as _parse_decimal, parse_effective_at as _parse_effective_at
from stock_ledger.util.payloads import _dec, entry_dict, lot_dict, stock_unit_dict
from stock_ledger.util.trace import trace_backward, trace_forward
from stock_ledger.views._common import _common_write_kwargs, _parse_json_body
from users_rbac.permissions import gate_floor_write, gate_warehouse_write


@csrf_exempt
@require_GET
def product_label_api(request, product_id: int):
    """Reusable product label: product id barcode + name / use by / trace text."""
    product = (
        Product.objects
        .select_related('unit')
        .filter(pk=product_id)
        .first()
    )
    if product is None:
        return api_error(f'product_id={product_id} not found.', status_code=404)

    lot = None
    lot_id = request.GET.get('lot_id')
    if lot_id not in (None, ''):
        try:
            lot = StockLot.objects.filter(
                pk=int(lot_id), product_id=product_id,
            ).first()
        except (TypeError, ValueError):
            return api_error('lot_id must be an integer.')
        if lot is None:
            return api_error(
                f'lot_id={lot_id} not found for this product.',
                status_code=404,
            )

    return api_success(
        'Product label ready.',
        stock_units.build_product_label(product=product, lot=lot),
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_print_api(request):
    """Print physical labels against a receipt or production_output entry."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        source_entry = StockEntry.objects.select_related(
            'lot__product', 'unit', 'location',
        ).get(pk=body['entry_id'])
        audit = _common_write_kwargs(request, body)
        units = stock_units.create_units_for_entry(
            source_entry=source_entry,
            unit_count=int(body['unit_count']),
            quantity_per_unit=_parse_decimal(
                body['quantity_per_unit'], 'quantity_per_unit',
            ),
            idempotency_key_prefix=str(body['idempotency_key_prefix']),
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockEntry.DoesNotExist:
        return api_error('entry_id not found.', status_code=404)
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except (ValueError, StockValidationError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Stock units printed.',
        [stock_unit_dict(u) for u in units],
        status_code=201,
    )


@csrf_exempt
@require_GET
def stock_units_detail_api(request, unit_serial: str):
    """Scan lookup: resolve a physical unit serial to product/stock info."""
    try:
        unit = stock_units.get_unit_by_serial(unit_serial)
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)

    balance = (
        StockBalance.objects
        .filter(lot_id=unit.lot_id, location_id=unit.location_id)
        .only('quantity', 'quantity_base', 'updated_at')
        .first()
    )
    product = unit.lot.product
    data = stock_unit_dict(unit)
    data['product'] = {
        'id': product.id,
        'name': product.name,
        'external_barcode': product.external_barcode,
    }
    data['lot'] = lot_dict(unit.lot)
    data['location'] = {
        'id': unit.location_id,
        'name': unit.location.name,
    }
    data['unit'] = {
        'id': unit.unit_id,
        'name': unit.unit.name,
    }
    data['location_balance'] = (
        {
            'quantity': _dec(balance.quantity),
            'quantity_base': _dec(balance.quantity_base),
            'updated_at': (
                balance.updated_at.isoformat() if balance.updated_at else None
            ),
        }
        if balance is not None
        else None
    )

    trace_mode = (request.GET.get('trace') or '').strip().lower()
    if trace_mode in ('backward', 'forward'):
        rows = (
            trace_backward(lot_id=unit.lot_id)
            if trace_mode == 'backward'
            else trace_forward(lot_id=unit.lot_id)
        )
        for row in rows:
            if isinstance(row.get('quantity_base'), Decimal):
                row['quantity_base'] = _dec(row['quantity_base'])
        data['trace'] = rows
    elif trace_mode:
        return api_error('trace must be backward or forward.')

    return api_success('Stock unit fetched.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_floor_write
def stock_units_consume_api(request, unit_serial: str):
    """Scan-to-consume: issue / disposal / production_consumption via unit serial."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        result = stock_units.consume_unit(
            unit_serial=unit_serial,
            entry_type=str(body['entry_type']),
            quantity=_parse_decimal(body['quantity'], 'quantity'),
            idempotency_key=body['idempotency_key'],
            output_entry_id=(
                int(body['output_entry_id'])
                if body.get('output_entry_id') not in (None, '')
                else None
            ),
            effective_at=_parse_effective_at(body.get('effective_at')),
            **_common_write_kwargs(request, body),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    except (ValueError, TypeError) as exc:
        return api_error(str(exc))
    return api_success(
        'Stock unit consumed.',
        {
            'entry': entry_dict(result['entry']),
            'unit': stock_unit_dict(result['unit']),
            'consumption_id': result['consumption'].id,
        },
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_void_api(request, unit_serial: str):
    """Void a damaged or misprinted label (no stock_balance change)."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')
    try:
        audit = _common_write_kwargs(request, body)
        unit = stock_units.void_unit(
            unit_serial=unit_serial,
            reason=str(body['reason']),
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except KeyError as exc:
        return api_error(f'Missing required field: {exc.args[0]}')
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    return api_success('Stock unit voided.', stock_unit_dict(unit))


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write()
def stock_units_reprint_api(request, unit_serial: str):
    """Reprint same serial; records a print event for audit."""
    body = _parse_json_body(request)
    if body is None:
        body = {}
    try:
        audit = _common_write_kwargs(request, body)
        result = stock_units.reprint_unit(
            unit_serial=unit_serial,
            actor_user_id=audit.get('actor_user_id'),
            lan_username=audit.get('lan_username'),
            source_workstation=audit.get('source_workstation'),
        )
    except StockValidationError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg else 400
        return api_error(msg, status_code=status)
    data = stock_unit_dict(result['unit'])
    data['print_event_id'] = result['print_event_id']
    return api_success('Stock unit reprint recorded.', data, status_code=201)
