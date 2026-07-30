import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import AllergenCode, Product, ProductAllergen


def allergen_dict(a: ProductAllergen) -> dict:
    return {
        'id': a.id,
        'product_id': a.product_id,
        'allergen_code': a.allergen_code,
        'contains': a.contains,
        'may_contain': a.may_contain,
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_allergens_api(request, pk: int):
    if request.method == 'GET':
        allergens = ProductAllergen.objects.filter(product_id=pk)
        return api_success(
            'Allergens fetched successfully.',
            [allergen_dict(a) for a in allergens],
        )

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    allergen_code = body.get('allergen_code')
    if allergen_code in (None, ''):
        return api_error('Missing required fields: allergen_code', status_code=400)
    if allergen_code not in AllergenCode.values:
        return api_error('Invalid allergen_code.', status_code=400)

    try:
        allergen = ProductAllergen.objects.create(
            product_id=pk,
            allergen_code=allergen_code,
            contains=bool(body.get('contains', False)),
            may_contain=bool(body.get('may_contain', False)),
        )
    except IntegrityError:
        allergen = ProductAllergen.objects.get(
            product_id=pk,
            allergen_code=allergen_code,
        )
        return api_success(
            f'Allergen {allergen_code} already exists for this product.',
            allergen_dict(allergen),
        )

    capture_product_audit(
        request,
        product_id=pk,
        entity='allergen',
        action='create',
        before_data=None,
        after_data=allergen_dict(allergen),
    )
    return api_success(
        'Allergen created successfully.',
        allergen_dict(allergen),
        status_code=201,
    )


@require_http_methods(['PATCH', 'DELETE'])
@csrf_exempt
def product_allergen_detail_api(request, pk: int, allergen_code: str):
    try:
        allergen = ProductAllergen.objects.get(
            product_id=pk,
            allergen_code=allergen_code,
        )
    except ProductAllergen.DoesNotExist:
        return api_error('Allergen not found.', status_code=404)

    if request.method == 'DELETE':
        before_data = allergen_dict(allergen)
        allergen.delete()
        capture_product_audit(
            request,
            product_id=pk,
            entity='allergen',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Allergen deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    before_data = allergen_dict(allergen)
    if 'contains' in body:
        allergen.contains = bool(body['contains'])
    if 'may_contain' in body:
        allergen.may_contain = bool(body['may_contain'])
    allergen.save()
    after_data = allergen_dict(allergen)
    capture_product_audit(
        request,
        product_id=pk,
        entity='allergen',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )

    return api_success('Allergen updated successfully.', after_data)
