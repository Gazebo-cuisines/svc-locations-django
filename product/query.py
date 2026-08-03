"""Product query helpers."""

from django.db.models import Prefetch

from product.models import Product
from recipe.models import RecipeVersion


def active_products(*, source_container_id=None, destination_container_id=None):
    qs = Product.objects.filter(is_active=True).select_related(
        'category', 'unit', 'shelf_life', 'recipe',
    ).prefetch_related(
        Prefetch(
            'recipe__versions',
            queryset=RecipeVersion.objects.order_by('-version_number'),
        ),
    )
    if source_container_id is not None:
        qs = qs.filter(source_container_id=source_container_id)
    if destination_container_id is not None:
        qs = qs.filter(destination_container_id=destination_container_id)
    return qs
