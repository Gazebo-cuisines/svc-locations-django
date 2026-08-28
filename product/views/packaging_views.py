import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import (
    DeliveryState,
    PackagingType,
    PhysicalState,
    Product,
    ProductPackaging,
)
from stock_ledger.util.conversions import sync_product_unit_conversion_for_product


def _dec(value):
    return str(value) if value is not None else None


def packaging_dict(row: ProductPackaging) -> dict:
    return {
        'product_id': row.product_id,
        'pack_weight': _dec(row.pack_weight),
        'unitary_weight': _dec(row.unitary_weight),
        'gross_unitary_weight': _dec(row.gross_unitary_weight),
        'align_unitary_weight': row.align_unitary_weight,
        'default_length': _dec(row.default_length),
        'items_per_unit': _dec(row.items_per_unit),
        'units_per_tray': _dec(row.units_per_tray),
        'units_per_batch': _dec(row.units_per_batch),
        'is_gas_flush': row.is_gas_flush,
        'container_vessel_id': row.container_vessel_id,
        'tray_id': row.tray_id,
        'box_id': row.box_id,
        'packaging_type_id': row.packaging_type_id,
        'physical_state_id': row.physical_state_id,
        'delivery_state_id': row.delivery_state_id,
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_decimal(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


def _parse_fk_id(body: dict, field: str, model):
    if field not in body:
        return None, False
    value = body.get(field)
    if value is None or value == '':
        return None, True
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid integer for {field}.')
    if not model.objects.filter(pk=value).exists():
        raise ValueError(f'{field}={value} not found.')
    return value, True


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_packaging_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            row = ProductPackaging.objects.get(pk=pk)
        except ProductPackaging.DoesNotExist:
            return api_success('Product packaging is not set yet.', data=None)
        return api_success('Product packaging fetched successfully.', packaging_dict(row))

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductPackaging.objects.filter(pk=pk).first()
        before_data = packaging_dict(existing) if existing else None
        deleted, _ = ProductPackaging.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product packaging not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='packaging',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product packaging deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'pack_weight': _parse_decimal(body.get('pack_weight'), 'pack_weight'),
            'unitary_weight': _parse_decimal(body.get('unitary_weight'), 'unitary_weight'),
            'gross_unitary_weight': _parse_decimal(
                body.get('gross_unitary_weight'),
                'gross_unitary_weight',
            ),
            'align_unitary_weight': bool(body.get('align_unitary_weight', False)),
            'default_length': _parse_decimal(body.get('default_length'), 'default_length'),
            'items_per_unit': _parse_decimal(body.get('items_per_unit'), 'items_per_unit'),
            'units_per_tray': _parse_decimal(body.get('units_per_tray'), 'units_per_tray'),
            'units_per_batch': _parse_decimal(body.get('units_per_batch'), 'units_per_batch'),
            'is_gas_flush': bool(body.get('is_gas_flush', False)),
        }

        fk_specs = (
            ('container_vessel_id', Product),
            ('tray_id', Product),
            ('box_id', Product),
            ('packaging_type_id', PackagingType),
            ('physical_state_id', PhysicalState),
            ('delivery_state_id', DeliveryState),
        )
        for field, model in fk_specs:
            value, provided = _parse_fk_id(body, field, model)
            if provided:
                defaults[field] = value

        existing = ProductPackaging.objects.filter(pk=pk).first()
        before_data = packaging_dict(existing) if existing else None
        row, created = ProductPackaging.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
        sync_product_unit_conversion_for_product(pk)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = packaging_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='packaging',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product packaging saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
