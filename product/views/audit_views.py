from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.query import active_products
from product.models import Product, ProductAudit


@require_GET
def product_timeline_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    row = ProductAudit.objects.filter(product_id=pk).first()
    events = []
    if row and isinstance(row.timeline_events, list):
        events = list(reversed(row.timeline_events))
    return api_success(
        'Product timeline fetched successfully.',
        events,
    )
