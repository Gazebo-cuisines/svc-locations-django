from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import ProductTechnical


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


@require_GET
def product_technical_api(request, pk: int):
    try:
        technical = ProductTechnical.objects.get(pk=pk)
    except ProductTechnical.DoesNotExist:
        return api_error('Technical data not found.', status_code=404)
    return api_success(
        'Technical data fetched successfully.',
        technical_dict(technical),
    )
