import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import Product, ProductTechnical


def technical_dict(t: ProductTechnical) -> dict:
    return {
        'product_id': t.product_id,
        'is_gmo_free': t.is_gmo_free,
        'is_vegetarian': t.is_vegetarian,
        'is_vegan': t.is_vegan,
        'country_of_origin': t.country_of_origin,
        'spec_sign_off_date': (
            t.spec_sign_off_date.isoformat() if t.spec_sign_off_date else None
        ),
        'next_review_date': (
            t.next_review_date.isoformat() if t.next_review_date else None
        ),
        'requires_temperature_check': t.requires_temperature_check,
        'temp_check_lower_bound': (
            str(t.temp_check_lower_bound)
            if t.temp_check_lower_bound is not None else None
        ),
        'temp_check_upper_bound': (
            str(t.temp_check_upper_bound)
            if t.temp_check_upper_bound is not None else None
        ),
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_date(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.')


def _parse_decimal(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_technical_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            technical = ProductTechnical.objects.get(pk=pk)
        except ProductTechnical.DoesNotExist:
            return api_success('Technical data is not set yet.', data=None)
        return api_success(
            'Technical data fetched successfully.',
            technical_dict(technical),
        )

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductTechnical.objects.filter(pk=pk).first()
        before_data = technical_dict(existing) if existing else None
        deleted, _ = ProductTechnical.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Technical data not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='technical',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Technical data deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            'is_gmo_free': bool(body.get('is_gmo_free', False)),
            'is_vegetarian': bool(body.get('is_vegetarian', False)),
            'is_vegan': bool(body.get('is_vegan', False)),
            'country_of_origin': body.get('country_of_origin'),
            'spec_sign_off_date': _parse_date(
                body.get('spec_sign_off_date'), 'spec_sign_off_date',
            ),
            'next_review_date': _parse_date(
                body.get('next_review_date'), 'next_review_date',
            ),
            'requires_temperature_check': bool(
                body.get('requires_temperature_check', False),
            ),
            'temp_check_lower_bound': _parse_decimal(
                body.get('temp_check_lower_bound'), 'temp_check_lower_bound',
            ),
            'temp_check_upper_bound': _parse_decimal(
                body.get('temp_check_upper_bound'), 'temp_check_upper_bound',
            ),
        }
        existing = ProductTechnical.objects.filter(pk=pk).first()
        before_data = technical_dict(existing) if existing else None
        technical, created = ProductTechnical.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    after_data = technical_dict(technical)
    capture_product_audit(
        request,
        product_id=pk,
        entity='technical',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Technical data saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
