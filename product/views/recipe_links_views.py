"""Product recipe coverage: parents (where used) and children (BOM)."""

from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.query import active_products
from recipe.models import Recipe, RecipeComponent, RecipeVersionStatus
from recipe.views.helpers import active_or_latest_version, dec


def _product_brief(product) -> dict:
    return {
        'id': product.id,
        'name': product.name,
        'recipe_code': product.recipe_code,
        'gff_code': product.gff_code,
        'is_active': product.is_active,
    }


def _pick_version_for_parent(versions):
    """Prefer active version that uses the component; else highest version_number."""
    versions = list(versions)
    for version in versions:
        if version.status == RecipeVersionStatus.ACTIVE:
            return version
    return max(versions, key=lambda v: v.version_number) if versions else None


@require_GET
def product_parents_api(request, pk: int):
    """Products whose recipes use this product as a component."""
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    comps = (
        RecipeComponent.objects.filter(component_product_id=pk)
        .select_related(
            'recipe_version__recipe__product',
            'unit',
        )
        .order_by(
            'recipe_version__recipe__product__recipe_code',
            'recipe_version__version_number',
            'line_no',
        )
    )

    by_parent = {}
    for comp in comps:
        parent = comp.recipe_version.recipe.product
        bucket = by_parent.setdefault(parent.id, {
            'product': parent,
            'versions': {},
        })
        bucket['versions'].setdefault(comp.recipe_version_id, {
            'version': comp.recipe_version,
            'lines': [],
        })
        bucket['versions'][comp.recipe_version_id]['lines'].append(comp)

    items = []
    for _parent_id, bucket in by_parent.items():
        versions = [entry['version'] for entry in bucket['versions'].values()]
        chosen = _pick_version_for_parent(versions)
        if not chosen:
            continue
        lines = bucket['versions'][chosen.id]['lines']
        items.append({
            **_product_brief(bucket['product']),
            'recipe_id': chosen.recipe_id,
            'recipe_version_id': chosen.id,
            'version_number': chosen.version_number,
            'version_status': chosen.status,
            'lines': [
                {
                    'line_no': line.line_no,
                    'quantity': dec(line.quantity),
                    'unit_id': line.unit_id,
                    'unit_name': line.unit.name if line.unit_id else None,
                }
                for line in sorted(lines, key=lambda x: x.line_no)
            ],
        })

    items.sort(key=lambda row: (row.get('recipe_code') or '', row['id']))
    return api_success(
        'Parent products fetched successfully.',
        {
            'product_id': pk,
            'count': len(items),
            'items': items,
        },
    )


@require_GET
def product_children_api(request, pk: int):
    """Components used to make this product (active or latest recipe version)."""
    if not active_products().filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    recipe = (
        Recipe.objects.filter(product_id=pk)
        .prefetch_related(
            'versions__components__component_product',
            'versions__components__unit',
        )
        .first()
    )
    empty = {
        'product_id': pk,
        'recipe_id': recipe.id if recipe else None,
        'recipe_version_id': None,
        'version_number': None,
        'version_status': None,
        'count': 0,
        'items': [],
    }
    if not recipe:
        return api_success('Child products fetched successfully.', empty)

    version = active_or_latest_version(recipe)
    if not version:
        return api_success('Child products fetched successfully.', empty)

    items = []
    for line in sorted(version.components.all(), key=lambda c: c.line_no):
        child = line.component_product
        items.append({
            'line_no': line.line_no,
            **(_product_brief(child) if child else {
                'id': None,
                'name': None,
                'recipe_code': None,
                'gff_code': None,
                'is_active': None,
            }),
            'quantity': dec(line.quantity),
            'unit_id': line.unit_id,
            'unit_name': line.unit.name if line.unit_id else None,
        })

    return api_success(
        'Child products fetched successfully.',
        {
            'product_id': pk,
            'recipe_id': recipe.id,
            'recipe_version_id': version.id,
            'version_number': version.version_number,
            'version_status': version.status,
            'count': len(items),
            'items': items,
        },
    )
