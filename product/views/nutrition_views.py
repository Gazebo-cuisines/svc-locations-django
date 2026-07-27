from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import ProductNutrition


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


@require_GET
def product_nutrition_api(request, pk: int):
    try:
        nutrition = ProductNutrition.objects.get(pk=pk)
    except ProductNutrition.DoesNotExist:
        return api_error('Nutrition not found.', status_code=404)
    return api_success(
        'Nutrition fetched successfully.',
        nutrition_dict(nutrition),
    )
