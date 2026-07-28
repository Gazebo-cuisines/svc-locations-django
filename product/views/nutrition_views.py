import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Product, ProductNutrition


def _dec(value):
    return str(value) if value is not None else None


def nutrition_dict(n: ProductNutrition) -> dict:
    return {
        'product_id': n.product_id,
        'energy_kj': _dec(n.energy_kj),
        'energy_kcal': _dec(n.energy_kcal),
        'fat_g': _dec(n.fat_g),
        'saturates_g': _dec(n.saturates_g),
        'carbohydrate_g': _dec(n.carbohydrate_g),
        'sugars_g': _dec(n.sugars_g),
        'fibre_g': _dec(n.fibre_g),
        'protein_g': _dec(n.protein_g),
        'salt_g': _dec(n.salt_g),
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


_NUTRITION_FIELDS = (
    'energy_kj',
    'energy_kcal',
    'fat_g',
    'saturates_g',
    'carbohydrate_g',
    'sugars_g',
    'fibre_g',
    'protein_g',
    'salt_g',
)


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_nutrition_api(request, pk: int):
    if request.method == 'GET':
        try:
            nutrition = ProductNutrition.objects.get(pk=pk)
        except ProductNutrition.DoesNotExist:
            return api_error('Nutrition not found.', status_code=404)
        return api_success(
            'Nutrition fetched successfully.',
            nutrition_dict(nutrition),
        )

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        deleted, _ = ProductNutrition.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Nutrition not found.', status_code=404)
        return api_success('Nutrition deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        defaults = {
            field: _parse_decimal(body.get(field), field)
            for field in _NUTRITION_FIELDS
        }
        nutrition, created = ProductNutrition.objects.update_or_create(
            product_id=pk,
            defaults=defaults,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    return api_success(
        'Nutrition saved successfully.',
        nutrition_dict(nutrition),
        status_code=201 if created else 200,
    )
