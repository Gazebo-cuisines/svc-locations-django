import json

from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import (
    AllergenCode,
    Category,
    DeliveryState,
    PackagingType,
    PhysicalState,
    ProductClass,
    PurchaseShapeFormat,
    Range,
    SubRange,
    Unit,
)


def _rows(queryset):
    return [{'id': row.id, 'name': row.name} for row in queryset.order_by('name')]


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _format_dict(row: PurchaseShapeFormat) -> dict:
    return {'id': row.id, 'name': row.name}


@require_GET
def product_class_list_api(request):
    return api_success('Product classes fetched successfully.', _rows(ProductClass.objects.all()))


@require_GET
def product_category_list_api(request):
    return api_success('Product categories fetched successfully.', _rows(Category.objects.all()))


@require_GET
def product_range_list_api(request):
    return api_success('Product ranges fetched successfully.', _rows(Range.objects.all()))


@require_GET
def product_sub_range_list_api(request):
    qs = SubRange.objects.all()
    range_id = request.GET.get('range_id')
    if range_id not in (None, ''):
        qs = qs.filter(range_id=range_id)
    rows = [
        {
            'id': row.id,
            'name': row.name,
            'range_id': row.range_id,
        }
        for row in qs.order_by('name')
    ]
    return api_success('Product sub-ranges fetched successfully.', rows)


@require_GET
def product_unit_list_api(request):
    return api_success('Product units fetched successfully.', _rows(Unit.objects.all()))


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_purchase_format_list_api(request):
    if request.method == 'GET':
        return api_success(
            'Purchase formats fetched successfully.',
            _rows(PurchaseShapeFormat.objects.all()),
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    name = (body.get('name') or '').strip()
    format_id = body.get('id')
    if format_id in (None, '') or not name:
        return api_error('Missing required fields: id, name', status_code=400)

    if PurchaseShapeFormat.objects.filter(pk=format_id).exists():
        return api_error(f'Purchase format id={format_id} already exists.', status_code=409)

    try:
        row = PurchaseShapeFormat.objects.create(id=format_id, name=name)
    except IntegrityError as exc:
        return api_error(f'Could not create purchase format: {exc}', status_code=400)

    return api_success('Purchase format created successfully.', _format_dict(row), status_code=201)


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_purchase_format_detail_api(request, pk: int):
    try:
        row = PurchaseShapeFormat.objects.get(pk=pk)
    except PurchaseShapeFormat.DoesNotExist:
        return api_error('Purchase format not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Purchase format fetched successfully.', _format_dict(row))

    if request.method == 'DELETE':
        try:
            row.delete()
        except ProtectedError:
            return api_error(
                'Purchase format is in use and cannot be deleted.',
                status_code=409,
            )
        return api_success('Purchase format deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    if 'name' not in body:
        return api_error('Missing required fields: name', status_code=400)

    name = (body.get('name') or '').strip()
    if not name:
        return api_error('name cannot be empty.', status_code=400)

    row.name = name
    try:
        row.save(update_fields=['name'])
    except IntegrityError as exc:
        return api_error(f'Could not update purchase format: {exc}', status_code=400)

    return api_success('Purchase format updated successfully.', _format_dict(row))


@require_GET
def product_packaging_type_list_api(request):
    return api_success(
        'Packaging types fetched successfully.',
        _rows(PackagingType.objects.all()),
    )


@require_GET
def product_physical_state_list_api(request):
    return api_success(
        'Physical states fetched successfully.',
        _rows(PhysicalState.objects.all()),
    )


@require_GET
def product_delivery_state_list_api(request):
    return api_success(
        'Delivery states fetched successfully.',
        _rows(DeliveryState.objects.all()),
    )


@require_GET
def product_allergen_code_list_api(request):
    rows = [
        {'code': code.value, 'name': code.label}
        for code in AllergenCode
    ]
    return api_success('Allergen codes fetched successfully.', rows)
