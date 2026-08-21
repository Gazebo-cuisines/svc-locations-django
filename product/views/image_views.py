import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductImage
from product.product_images import (
    delete_product_image,
    list_product_images,
    product_image_dict,
    update_product_image,
    upload_product_image,
)
from product.query import active_products


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _as_bool(value):
    if value in (True, False):
        return value
    if value in (None, ''):
        return None
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes'):
        return True
    if text in ('0', 'false', 'no'):
        return False
    raise ValueError('is_main must be true or false.')


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_images_api(request, pk: int):
    try:
        product = active_products().get(pk=pk)
    except Product.DoesNotExist:
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        data = list_product_images(pk)
        return api_success(
            'Product images fetched successfully.',
            {'count': len(data), 'results': data},
        )

    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return api_error('Image file is required (multipart field: file).', status_code=400)

    try:
        is_main = _as_bool(request.POST.get('is_main')) or False
        sort_raw = request.POST.get('sort_order')
        sort_order = int(sort_raw) if sort_raw not in (None, '') else 0
        data = upload_product_image(
            product, uploaded, is_main=is_main, sort_order=sort_order,
        )
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    capture_product_audit(
        request,
        product_id=pk,
        entity='image',
        action='create',
        before_data=None,
        after_data=data,
    )
    return api_success('Product image uploaded successfully.', data, status_code=201)


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_image_detail_api(request, pk: int):
    try:
        row = ProductImage.objects.select_related('product').get(pk=pk)
    except ProductImage.DoesNotExist:
        return api_error('Product image not found.', status_code=404)

    if not active_products().filter(pk=row.product_id).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Product image fetched successfully.', product_image_dict(row))

    if request.method == 'DELETE':
        before_data = product_image_dict(row)
        product_id = row.product_id
        delete_product_image(row)
        capture_product_audit(
            request,
            product_id=product_id,
            entity='image',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Product image deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    before_data = product_image_dict(row)
    try:
        is_main = _as_bool(body['is_main']) if 'is_main' in body else None
        sort_order = None
        if 'sort_order' in body:
            sort_order = int(body['sort_order'])
        data = update_product_image(row, is_main=is_main, sort_order=sort_order)
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    capture_product_audit(
        request,
        product_id=row.product_id,
        entity='image',
        action='update',
        before_data=before_data,
        after_data=data,
    )
    return api_success('Product image updated successfully.', data)
