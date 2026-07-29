from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import Category, Product, ProductClass, Range, Unit


def product_class_dict(product_class) -> dict:
    return {
        'id': product_class.id,
        'name': product_class.name,
    }


def product_category_dict(category) -> dict:
    return {
        'id': category.id,
        'name': category.name,
        'parent_id': category.parent_id,
    }


def _category_tree(categories) -> list[dict]:
    nodes = {
        c.id: {
            'id': c.id,
            'name': c.name,
            'parent_id': c.parent_id,
            'children': [],
        }
        for c in categories
    }
    roots = []
    for node in nodes.values():
        parent_id = node['parent_id']
        if parent_id and parent_id in nodes:
            nodes[parent_id]['children'].append(node)
        else:
            roots.append(node)
    return roots


def product_range_dict(product_range) -> dict:
    return {
        'id': product_range.id,
        'name': product_range.name,
    }


def product_unit_dict(product_unit) -> dict:
    return {
        'id': product_unit.id,
        'name': product_unit.name,
    }


def _list_success(message: str, results: list[dict]):
    return api_success(
        message,
        data={'count': len(results), 'results': results},
    )


def _get_product(pk: int):
    try:
        return Product.objects.select_related(
            'product_class',
            'range',
            'unit',
        ).get(pk=pk)
    except Product.DoesNotExist:
        return None


@require_GET
def product_class_list_api(request):
    results = [product_class_dict(row) for row in ProductClass.objects.all()]
    return _list_success('Product class list fetched successfully.', results)


@require_GET
def product_category_list_api(request):
    tree = _category_tree(Category.objects.all())
    return _list_success('Category list fetched successfully.', tree)


@require_GET
def product_range_list_api(request):
    results = [product_range_dict(row) for row in Range.objects.all()]
    return _list_success('Product range list fetched successfully.', results)


@require_GET
def product_unit_list_api(request):
    results = [product_unit_dict(row) for row in Unit.objects.all()]
    return _list_success('Product unit list fetched successfully.', results)


@require_GET
def product_class_api(request, pk: int):
    product = _get_product(pk)
    if product is None:
        return api_error('Product not found.', status_code=404)
    if product.product_class is None:
        return api_error('Class data not found.', status_code=404)
    return api_success(
        'Data fetched successfully.',
        product_class_dict(product.product_class),
    )


@require_GET
def product_range_api(request, pk: int):
    product = _get_product(pk)
    if product is None:
        return api_error('Product not found.', status_code=404)
    if product.range is None:
        return api_error('Range data not found.', status_code=404)
    return api_success(
        'Data fetched successfully.',
        product_range_dict(product.range),
    )


@require_GET
def product_unit_api(request, pk: int):
    product = _get_product(pk)
    if product is None:
        return api_error('Product not found.', status_code=404)
    if product.unit is None:
        return api_error('Unit data not found.', status_code=404)
    return api_success(
        'Data fetched successfully.',
        product_unit_dict(product.unit),
    )
