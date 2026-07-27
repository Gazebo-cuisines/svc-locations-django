from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import Product


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


@require_GET
def product_list_api(request):
    products = Product.objects.filter(is_active=True)
    return api_success(
        'Product list fetched successfully.',
        [product_list_dict(p) for p in products],
    )


@require_GET
def product_detail_api(request, pk: int):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return api_error('Product not found.', status_code=404)
    return api_success('Product fetched successfully.', product_detail_dict(product))
