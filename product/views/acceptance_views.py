import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductAcceptance


def acceptance_dict(a: ProductAcceptance) -> dict:
    return {
        'product_id': a.product_id,
        'min_acceptable_shelf_life_days': a.min_acceptable_shelf_life_days,
        'acceptance_note': a.acceptance_note,
    }


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@require_http_methods(['GET', 'PUT', 'DELETE'])
@csrf_exempt
def product_acceptance_api(request, pk: int):
    if request.method == 'GET':
        try:
            acceptance = ProductAcceptance.objects.get(pk=pk)
        except ProductAcceptance.DoesNotExist:
            return api_success('Acceptance data is not set yet.', data=None)
        return api_success(
            'Acceptance data fetched successfully.',
            acceptance_dict(acceptance),
        )

    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'DELETE':
        existing = ProductAcceptance.objects.filter(pk=pk).first()
        before_data = acceptance_dict(existing) if existing else None
        deleted, _ = ProductAcceptance.objects.filter(pk=pk).delete()
        if not deleted:
            return api_error('Acceptance data not found.', status_code=404)
        capture_product_audit(
            request,
            product_id=pk,
            entity='acceptance',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Acceptance data deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    shelf_life = body.get('min_acceptable_shelf_life_days')
    if shelf_life is not None and shelf_life != '':
        try:
            shelf_life = int(shelf_life)
        except (TypeError, ValueError):
            return api_error(
                'min_acceptable_shelf_life_days must be an integer.',
                status_code=400,
            )
    else:
        shelf_life = None

    existing = ProductAcceptance.objects.filter(pk=pk).first()
    before_data = acceptance_dict(existing) if existing else None
    acceptance, created = ProductAcceptance.objects.update_or_create(
        product_id=pk,
        defaults={
            'min_acceptable_shelf_life_days': shelf_life,
            'acceptance_note': body.get('acceptance_note'),
        },
    )
    after_data = acceptance_dict(acceptance)
    capture_product_audit(
        request,
        product_id=pk,
        entity='acceptance',
        action='create' if created else 'update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Acceptance data saved successfully.',
        after_data,
        status_code=201 if created else 200,
    )
