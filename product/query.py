"""Product query helpers."""

from django.db.models import Prefetch, Q

from product.models import Category, Product
from recipe.models import RecipeVersion


def product_search_q(q: str) -> Q:
    return (
        Q(name__icontains=q)
        | Q(alternate_name__icontains=q)
        | Q(recipe_code__icontains=q)
        | Q(gff_code__icontains=q)
        | Q(external_barcode__icontains=q)
    )


def category_subtree_ids(root_id: int) -> list[int]:
    """
    Category id plus every descendant.

    Uses legacy path_nodes, e.g. root '(73)', DAIRY '(73,27)', child '(73,27,99)'.
    Filtering by a non-root id must use that row's full path_nodes, not '(id)'.
    """
    root_id = int(root_id)
    cat = (
        Category.objects.filter(pk=root_id)
        .only('id', 'path_nodes')
        .first()
    )
    if cat is None:
        return []

    nodes = (cat.path_nodes or '').strip()
    if not nodes:
        # Fallback: walk children by parent_id when path_nodes missing.
        ids = [root_id]
        frontier = [root_id]
        while frontier:
            children = list(
                Category.objects.filter(parent_id__in=frontier)
                .values_list('id', flat=True),
            )
            frontier = [c for c in children if c not in ids]
            ids.extend(frontier)
        return ids

    # '(73,27)' → self + anything under '(73,27,'
    prefix = nodes[:-1] + ',' if nodes.endswith(')') else f'{nodes},'
    return list(
        Category.objects.filter(
            Q(pk=root_id)
            | Q(path_nodes=nodes)
            | Q(path_nodes__startswith=prefix),
        ).values_list('id', flat=True),
    )


def active_products(
    *,
    source_container_id=None,
    destination_container_id=None,
    q=None,
    category_id=None,
):
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
    if category_id is not None:
        ids = category_subtree_ids(category_id)
        if not ids:
            return qs.none()
        qs = qs.filter(category_id__in=ids)
    return qs
