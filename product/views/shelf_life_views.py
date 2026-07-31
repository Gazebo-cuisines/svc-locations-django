import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import Product, ProductShelfLife


def shelf_life_dict(row: ProductShelfLife) -> dict:
    return {
        'product_id': row.product_id,
        'shelf_life_days': row.shelf_life_days,
        'shelf_life_intrinsic_days': row.shelf_life_intrinsic_days,
        'shelf_life_depot_days': row.shelf_life_depot_days,
        'absolute_min_shelf_life_days': row.absolute_min_shelf_life_days,
        'force_production_date': row.force_production_date,
        'force_trace_number': row.force_trace_number,
        'force_use_by': row.force_use_by,
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_int(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid integer for {field_name}.')


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_shelf_life_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            row = ProductShelfLife.objects.get(pk=pk)
        except ProductShelfLife.DoesNotExist:
            return api_success('Product shelf life is not set yet.', data=None)
        return api_success('Product shelf life fetched successfully.', shelf_life_dict(row))

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductShelfLife.objects.filter(pk=pk).first()
        before_data = shelf_life_dict(existing) if existing else None
        deleted, _ = ProductShelfLife.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product shelf life not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='shelf_life',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product shelf life deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'shelf_life_days': _parse_int(body.get('shelf_life_days'), 'shelf_life_days'),
            'shelf_life_intrinsic_days': _parse_int(
                body.get('shelf_life_intrinsic_days'),
                'shelf_life_intrinsic_days',
            ),
            'shelf_life_depot_days': _parse_int(
                body.get('shelf_life_depot_days'),
                'shelf_life_depot_days',
            ),
            'absolute_min_shelf_life_days': _parse_int(
                body.get('absolute_min_shelf_life_days'),
                'absolute_min_shelf_life_days',
            ),
            'force_production_date': bool(body.get('force_production_date', False)),
            'force_trace_number': bool(body.get('force_trace_number', False)),
            'force_use_by': bool(body.get('force_use_by', False)),
        }
        existing = ProductShelfLife.objects.filter(pk=pk).first()
        before_data = shelf_life_dict(existing) if existing else None
        row, created = ProductShelfLife.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = shelf_life_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='shelf_life',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product shelf life saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
