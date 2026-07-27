from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import ProductAcceptance


def acceptance_dict(a: ProductAcceptance) -> dict:
    return {
        'product_id': a.product_id,
        'min_acceptable_shelf_life_days': a.min_acceptable_shelf_life_days,
        'acceptance_note': a.acceptance_note,
    }


@require_GET
def product_acceptance_api(request, pk: int):
    try:
        acceptance = ProductAcceptance.objects.get(pk=pk)
    except ProductAcceptance.DoesNotExist:
        return api_error('Acceptance data not found.', status_code=404)
    return api_success(
        'Acceptance data fetched successfully.',
        acceptance_dict(acceptance),
    )
