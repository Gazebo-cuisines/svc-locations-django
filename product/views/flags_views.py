import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductFlags

# One API for all product attribute flags.
# Excluded: record_flag (dropped from API). Chilling loss factor lives on ProductYield.
FLAG_FIELDS = (
    'in_stock_list',
    'auto_yield',
    'has_plan',
    'auto_rate',
    'auto_clear_stock',
    'consider_stock_in_plan',
    'has_recipe',
    'full_batches_only',
    'auto_trends',
    'is_implicit',
    'include_in_projections',
    'has_chilling_loss',
    'use_batch_quantity',
    'freezer_no_deduct',
    'is_purchase_item',
    'is_sales_item',
    'is_dispatch_support',
)


def flags_dict(flags: ProductFlags) -> dict:
    data = {'product_id': flags.product_id}
    for field in FLAG_FIELDS:
        data[field] = bool(getattr(flags, field))
    return data


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_flags_api(request, pk: int):
    if request.method == 'GET':
        try:
            flags = ProductFlags.objects.get(pk=pk)
        except ProductFlags.DoesNotExist:
            return api_error('Product flags not found.', status_code=404)
        return api_success('Product flags fetched successfully.', flags_dict(flags))

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductFlags.objects.filter(pk=pk).first()
        before_data = flags_dict(existing) if existing else None
        deleted, _ = ProductFlags.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Product flags not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='flags',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product flags deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    # Full replace: omitted keys default to False (same pattern as technical booleans).
    defaults = {field: bool(body.get(field, False)) for field in FLAG_FIELDS}
    existing = ProductFlags.objects.filter(pk=pk).first()
    before_data = flags_dict(existing) if existing else None
    flags, created = ProductFlags.objects.update_or_create(
        product_id=pk,
        defaults=defaults,
    )
    after_data = flags_dict(flags)
    capture_product_audit(
        request,
        product_id=pk,
        entity='flags',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product flags saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
