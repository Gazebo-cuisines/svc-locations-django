import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductProduction


def _dec(value):
    return str(value) if value is not None else None


def production_dict(row: ProductProduction) -> dict:
    return {
        'product_id': row.product_id,
        'avg_run_size': _dec(row.avg_run_size),
        'avg_minutes': _dec(row.avg_minutes),
        'avg_rate_product': _dec(row.avg_rate_product),
        'avg_staff_min_per_unit': _dec(row.avg_staff_min_per_unit),
        'avg_staff_per_minute': _dec(row.avg_staff_per_minute),
        'avg_rate_range': _dec(row.avg_rate_range),
        'average_rate': _dec(row.average_rate),
        'unitary_gap_time': _dec(row.unitary_gap_time),
        'unitary_dwell_time': _dec(row.unitary_dwell_time),
        'relative_plan_position': row.relative_plan_position,
        'default_resource_id': row.default_resource_id,
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


def _parse_int(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid integer for {field_name}.')


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_production_api(request, pk: int):
    if request.method == 'GET':
        try:
            row = ProductProduction.objects.get(pk=pk)
        except ProductProduction.DoesNotExist:
            return api_success('Product production is not set yet.', data=None)
        return api_success('Product production fetched successfully.', production_dict(row))

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductProduction.objects.filter(pk=pk).first()
        before_data = production_dict(existing) if existing else None
        deleted, _ = ProductProduction.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product production not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='production',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product production deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    decimal_fields = (
        'avg_run_size',
        'avg_minutes',
        'avg_rate_product',
        'avg_staff_min_per_unit',
        'avg_staff_per_minute',
        'avg_rate_range',
        'average_rate',
        'unitary_gap_time',
        'unitary_dwell_time',
    )
    try:
        defaults = {
            field: _parse_decimal(body.get(field), field)
            for field in decimal_fields
        }
        defaults['relative_plan_position'] = _parse_int(
            body.get('relative_plan_position'),
            'relative_plan_position',
        )
        defaults['default_resource_id'] = _parse_int(
            body.get('default_resource_id'),
            'default_resource_id',
        )
        existing = ProductProduction.objects.filter(pk=pk).first()
        before_data = production_dict(existing) if existing else None
        row, created = ProductProduction.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = production_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='production',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product production saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
