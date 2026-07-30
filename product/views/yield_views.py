import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductYield


def yield_dict(y: ProductYield) -> dict:
    return {
        'product_id': y.product_id,
        'yield_factor': str(y.yield_factor),
        'yield_factor_auto': str(y.yield_factor_auto),
        'chilling_loss_factor': (
            str(y.chilling_loss_factor)
            if y.chilling_loss_factor is not None else None
        ),
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_decimal(value, field_name: str, *, required: bool = False):
    if value is None or value == '':
        if required:
            raise ValueError(f'{field_name} is required.')
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_yield_api(request, pk: int):
    if request.method == 'GET':
        try:
            row = ProductYield.objects.get(pk=pk)
        except ProductYield.DoesNotExist:
            return api_error('Product yield not found.', status_code=404)
        return api_success('Product yield fetched successfully.', yield_dict(row))

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductYield.objects.filter(pk=pk).first()
        before_data = yield_dict(existing) if existing else None
        deleted, _ = ProductYield.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product yield not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='yield',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product yield deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'yield_factor': _parse_decimal(
                body.get('yield_factor', '1.0000'), 'yield_factor', required=True,
            ),
            'yield_factor_auto': _parse_decimal(
                body.get('yield_factor_auto', '1.0000'), 'yield_factor_auto', required=True,
            ),
            'chilling_loss_factor': _parse_decimal(
                body.get('chilling_loss_factor'), 'chilling_loss_factor',
            ),
        }
        existing = ProductYield.objects.filter(pk=pk).first()
        before_data = yield_dict(existing) if existing else None
        row, created = ProductYield.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = yield_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='yield',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product yield saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
