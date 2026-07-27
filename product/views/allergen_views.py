from django.views.decorators.http import require_GET

from locations.utils.api_response import api_success
from product.models import ProductAllergen


def allergen_dict(a: ProductAllergen) -> dict:
    return {
        'id': a.id,
        'product_id': a.product_id,
        'allergen_code': a.allergen_code,
        'contains': a.contains,
        'may_contain': a.may_contain,
    }


@require_GET
def product_allergens_api(request, pk: int):
    allergens = ProductAllergen.objects.filter(product_id=pk)
    return api_success(
        'Allergens fetched successfully.',
        [allergen_dict(a) for a in allergens],
    )
