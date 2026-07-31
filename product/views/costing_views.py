import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import Product, ProductCosting


def costing_dict(c: ProductCosting) -> dict:
    return {
        'product_id': c.product_id,
        'unit_cost': str(c.unit_cost),
        'unit_price': str(c.unit_price),
        'nominal_code': c.nominal_code,
        'case_size_description': c.case_size_description,
        'lead_time_days': c.lead_time_days,
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
def product_costing_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            row = ProductCosting.objects.get(pk=pk)
        except ProductCosting.DoesNotExist:
            return api_success('Product costing is not set yet.', data=None)
        return api_success('Product costing fetched successfully.', costing_dict(row))

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductCosting.objects.filter(pk=pk).first()
        before_data = costing_dict(existing) if existing else None
        deleted, _ = ProductCosting.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product costing not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='costing',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product costing deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'unit_cost': _parse_decimal(body.get('unit_cost', '0'), 'unit_cost'),
            'unit_price': _parse_decimal(body.get('unit_price', '0'), 'unit_price'),
            'nominal_code': body.get('nominal_code'),
            'case_size_description': body.get('case_size_description'),
            'lead_time_days': _parse_int(body.get('lead_time_days'), 'lead_time_days'),
        }
        existing = ProductCosting.objects.filter(pk=pk).first()
        before_data = costing_dict(existing) if existing else None
        row, created = ProductCosting.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = costing_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='costing',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product costing saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
