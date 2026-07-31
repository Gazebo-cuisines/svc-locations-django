import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import Product, ProductIngredientLabel


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


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


_LABEL_FIELDS = (
    'name',
    'description',
    'per_srp',
    'per_box',
    'free_text_a',
    'free_text_b',
    'size_text',
    'storage',
    'cooking_preparation',
    'average_weight',
    'per_pallet',
    'per_case',
    'ingredients_text',
)


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_ingredient_label_api(request, pk: int):
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        try:
            label = ProductIngredientLabel.objects.get(pk=pk)
        except ProductIngredientLabel.DoesNotExist:
            return api_success('Ingredient label is not set yet.', data=None)
        return api_success(
            'Ingredient label fetched successfully.',
            ingredient_label_dict(label),
        )

    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductIngredientLabel.objects.filter(pk=pk).first()
        before_data = ingredient_label_dict(existing) if existing else None
        deleted, _ = ProductIngredientLabel.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Ingredient label not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='ingredient_label',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Ingredient label deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    defaults = {field: body.get(field) for field in _LABEL_FIELDS}
    existing = ProductIngredientLabel.objects.filter(pk=pk).first()
    before_data = ingredient_label_dict(existing) if existing else None
    label, created = ProductIngredientLabel.objects.update_or_create(
        product_id=pk,
        defaults=defaults,
    )
    after_data = ingredient_label_dict(label)
    capture_product_audit(
        request,
        product_id=pk,
        entity='ingredient_label',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Ingredient label saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
