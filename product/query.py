"""Product query helpers."""

from django.db.models import Prefetch, Q

from product.models import Product
from recipe.models import RecipeVersion


def product_search_q(q: str) -> Q:
    return (
        Q(name__icontains=q)
        | Q(alternate_name__icontains=q)
        | Q(recipe_code__icontains=q)
        | Q(gff_code__icontains=q)
        | Q(external_barcode__icontains=q)
    )


def active_products(*, source_container_id=None, destination_container_id=None, q=None):
    qs = Product.objects.filter(is_active=True).select_related(
        'category', 'unit', 'shelf_life', 'recipe', 'product_class',
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
    if q:
        qs = qs.filter(product_search_q(q))
    return qs
