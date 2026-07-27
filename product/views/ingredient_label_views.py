from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import ProductIngredientLabel


def ingredient_label_dict(label: ProductIngredientLabel) -> dict:
    return {
        'product_id': label.product_id,
        'name': label.name,
        'description': label.description,
        'per_srp': label.per_srp,
        'per_box': label.per_box,
        'free_text_a': label.free_text_a,
        'free_text_b': label.free_text_b,
        'size_text': label.size_text,
        'storage': label.storage,
        'cooking_preparation': label.cooking_preparation,
        'average_weight': label.average_weight,
        'per_pallet': label.per_pallet,
        'per_case': label.per_case,
        'ingredients_text': label.ingredients_text,
    }


@require_GET
def product_ingredient_label_api(request, pk: int):
    try:
        label = ProductIngredientLabel.objects.get(pk=pk)
    except ProductIngredientLabel.DoesNotExist:
        return api_error('Ingredient label not found.', status_code=404)
    return api_success(
        'Ingredient label fetched successfully.',
        ingredient_label_dict(label),
    )
