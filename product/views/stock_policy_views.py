import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import Product, ProductStockPolicy


def _dec(value):
    return str(value) if value is not None else None


def stock_policy_dict(row: ProductStockPolicy) -> dict:
    return {
        'product_id': row.product_id,
        'reorder_level': _dec(row.reorder_level),
        'min_stock': _dec(row.min_stock),
        'max_stock': _dec(row.max_stock),
        'clear_stock_level': _dec(row.clear_stock_level),
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


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_stock_policy_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            row = ProductStockPolicy.objects.get(pk=pk)
        except ProductStockPolicy.DoesNotExist:
            return api_success('Product stock policy is not set yet.', data=None)
        return api_success(
            'Product stock policy fetched successfully.',
            stock_policy_dict(row),
        )

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductStockPolicy.objects.filter(pk=pk).first()
        before_data = stock_policy_dict(existing) if existing else None
        deleted, _ = ProductStockPolicy.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product stock policy not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='stock_policy',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product stock policy deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'reorder_level': _parse_decimal(body.get('reorder_level'), 'reorder_level'),
            'min_stock': _parse_decimal(body.get('min_stock'), 'min_stock'),
            'max_stock': _parse_decimal(body.get('max_stock'), 'max_stock'),
            'clear_stock_level': _parse_decimal(
                body.get('clear_stock_level'),
                'clear_stock_level',
            ),
        }
        existing = ProductStockPolicy.objects.filter(pk=pk).first()
        before_data = stock_policy_dict(existing) if existing else None
        row, created = ProductStockPolicy.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = stock_policy_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='stock_policy',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product stock policy saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
