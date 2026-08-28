from __future__ import annotations

from recipe.models import Recipe, RecipeVersion, RecipeVersionStatus
from recipe.utils import is_process_batch_recipe

from planning.adapters.types import (
    CONTRACT_VERSION,
    ComponentSpec,
    RecipeVersionSpec,
)
from planning.errors import PlanningError

__all__ = ['CONTRACT_VERSION', 'get_recipe_version', 'resolve_recipe_version_id']


def resolve_recipe_version_id(product_id: int, pinned_version_id: int | None) -> int | None:
    if pinned_version_id is not None:
        return pinned_version_id
    try:
        recipe = Recipe.objects.get(product_id=product_id)
    except Recipe.DoesNotExist:
        return None
    active = (
        RecipeVersion.objects
        .filter(recipe_id=recipe.id, status=RecipeVersionStatus.ACTIVE)
        .order_by('-version_number')
        .first()
    )
    return active.id if active else None


def get_recipe_version(version_id: int) -> RecipeVersionSpec:
    try:
        version = (
            RecipeVersion.objects
            .select_related('recipe__product')
            .prefetch_related('components')
            .get(pk=version_id)
        )
    except RecipeVersion.DoesNotExist as exc:
        raise PlanningError(f'recipe version {version_id} not found') from exc

    components = tuple(
        ComponentSpec(
            product_id=c.component_product_id,
            quantity=c.quantity,
            unit_id=c.unit_id,
            line_no=c.line_no,
        )
        for c in sorted(version.components.all(), key=lambda x: x.line_no)
    )
    product = version.recipe.product
    return RecipeVersionSpec(
        version_id=version.id,
        product_id=version.recipe.product_id,
        recipe_code=product.recipe_code or '',
        version_number=version.version_number,
        process_loss=version.process_loss,
        batch_quantity=version.batch_quantity,
        process_batch=is_process_batch_recipe(product.recipe_code, product.name),
        components=components,
    )
