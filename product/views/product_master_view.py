import json

from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location
from locations.utils.api_response import api_error, api_success
from product.models import (
    Category,
    Product,
    ProductClass,
    PurchaseShapeFormat,
    Range,
    SubRange,
    Unit,
)


def product_list_dict(product: Product) -> dict:
    return {
        'id': product.id,
        'name': product.name,
        'alternate_name': product.alternate_name,
        'recipe_code': product.recipe_code,
        'is_active': product.is_active,
        'product_class_id': product.product_class_id,
        'category_id': product.category_id,
        'range_id': product.range_id,
        'unit_id': product.unit_id,
    }


def product_detail_dict(product: Product) -> dict:
    data = product_list_dict(product)
    data.update({
        'alternate_recipe_code': product.alternate_recipe_code,
        'gff_code': product.gff_code,
        'secondary_gff_recipe': product.secondary_gff_recipe,
        'external_barcode': product.external_barcode,
        'is_downtime': product.is_downtime,
        'purchasing_version': product.purchasing_version,
        'ingredient_count': product.ingredient_count,
        'remarks': product.remarks,
        'sub_range_id': product.sub_range_id,
        'purchasing_unit_id': product.purchasing_unit_id,
        'purchase_shape_format_id': product.purchase_shape_format_id,
        'source_container_id': product.source_container_id,
        'destination_container_id': product.destination_container_id,
        'created_at': product.created_at.isoformat() if product.created_at else None,
        'updated_at': product.updated_at.isoformat() if product.updated_at else None,
    })
    return data


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_collection_api(request):
    if request.method == 'GET':
        products = Product.objects.filter(is_active=True)
        return api_success(
            'Product list fetched successfully.',
            [product_list_dict(p) for p in products],
        )
    return product_create_api(request)


@require_GET
def product_detail_api(request, pk: int):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return api_error('Product not found.', status_code=404)
    return api_success('Product fetched successfully.', product_detail_dict(product))


def product_create_api(request):
    """Create a core product row. Required FKs must already exist."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    required = [
        'id',
        'name',
        'product_class_id',
        'category_id',
        'range_id',
        'unit_id',
        'source_container_id',
        'destination_container_id',
    ]
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(
            f'Missing required fields: {", ".join(missing)}',
            status_code=400,
        )

    if Product.objects.filter(pk=body['id']).exists():
        return api_error(f'Product id={body["id"]} already exists.', status_code=409)

    try:
        ProductClass.objects.get(pk=body['product_class_id'])
        Category.objects.get(pk=body['category_id'])
        Range.objects.get(pk=body['range_id'])
        Unit.objects.get(pk=body['unit_id'])
    except (
        ProductClass.DoesNotExist,
        Category.DoesNotExist,
        Range.DoesNotExist,
        Unit.DoesNotExist,
    ) as exc:
        return api_error(f'Invalid lookup reference: {exc}', status_code=400)

    sub_range_id = body.get('sub_range_id')
    if sub_range_id is not None and not SubRange.objects.filter(pk=sub_range_id).exists():
        return api_error(f'sub_range_id={sub_range_id} not found.', status_code=400)

    purchasing_unit_id = body.get('purchasing_unit_id')
    if (
        purchasing_unit_id is not None
        and not Unit.objects.filter(pk=purchasing_unit_id).exists()
    ):
        return api_error(
            f'purchasing_unit_id={purchasing_unit_id} not found.',
            status_code=400,
        )

    purchase_shape_format_id = body.get('purchase_shape_format_id')
    if (
        purchase_shape_format_id is not None
        and not PurchaseShapeFormat.objects.filter(pk=purchase_shape_format_id).exists()
    ):
        return api_error(
            f'purchase_shape_format_id={purchase_shape_format_id} not found.',
            status_code=400,
        )

    for field in ('source_container_id', 'destination_container_id'):
        if not Location.objects.filter(pk=body[field]).exists():
            return api_error(f'{field}={body[field]} not found.', status_code=400)

    try:
        product = Product.objects.create(
            id=body['id'],
            name=body['name'],
            alternate_name=body.get('alternate_name'),
            recipe_code=body.get('recipe_code'),
            alternate_recipe_code=body.get('alternate_recipe_code'),
            gff_code=body.get('gff_code'),
            secondary_gff_recipe=body.get('secondary_gff_recipe'),
            external_barcode=body.get('external_barcode'),
            is_active=body.get('is_active', True),
            is_downtime=body.get('is_downtime', False),
            purchasing_version=body.get('purchasing_version'),
            ingredient_count=body.get('ingredient_count'),
            remarks=body.get('remarks'),
            product_class_id=body['product_class_id'],
            category_id=body['category_id'],
            range_id=body['range_id'],
            sub_range_id=sub_range_id,
            unit_id=body['unit_id'],
            purchasing_unit_id=purchasing_unit_id,
            purchase_shape_format_id=purchase_shape_format_id,
            source_container_id=body['source_container_id'],
            destination_container_id=body['destination_container_id'],
        )
    except IntegrityError as exc:
        return api_error(f'Could not create product: {exc}', status_code=400)

    return api_success(
        'Product created successfully.',
        product_detail_dict(product),
        status_code=201,
    )
