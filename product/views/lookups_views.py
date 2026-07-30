from django.views.decorators.http import require_GET

from locations.utils.api_response import api_success
from product.models import Category, ProductClass, Range, Unit


def _rows(queryset):
    return [{'id': row.id, 'name': row.name} for row in queryset.order_by('name')]


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
def product_unit_list_api(request):
    return api_success('Product units fetched successfully.', _rows(Unit.objects.all()))
