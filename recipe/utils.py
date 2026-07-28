from django.db import transaction

from product.models import ProductFlags
from recipe.models import RecipeComponent, RecipeVersion, RecipeVersionStatus


class RecipeValidationError(ValueError):
    """Raised when a recipe rule is violated."""


def assert_not_self_loop(*, parent_product_id: int, component_product_id: int) -> None:
    """Reject component_product_id == recipe.product_id."""
    if parent_product_id == component_product_id:
        raise RecipeValidationError(
            'A recipe cannot include its own product as a component.'
        )


def sync_has_recipe(product_id: int) -> None:
    """Set product_flags.has_recipe from whether any recipe components exist."""
    has_recipe = RecipeComponent.objects.filter(
        recipe_version__recipe__product_id=product_id,
    ).exists()
    ProductFlags.objects.update_or_create(
        product_id=product_id,
        defaults={'has_recipe': has_recipe},
    )


@transaction.atomic
def activate_version(version: RecipeVersion) -> RecipeVersion:
    """
    Make version active; retire any prior active version for the same recipe.
    Keeps history (no wipe of component lines).
    """
    locked = RecipeVersion.objects.select_for_update().filter(
        recipe_id=version.recipe_id,
    )
    target = locked.get(pk=version.pk)
    locked.filter(status=RecipeVersionStatus.ACTIVE).exclude(pk=target.pk).update(
        status=RecipeVersionStatus.RETIRED,
    )
    if target.status != RecipeVersionStatus.ACTIVE:
        target.status = RecipeVersionStatus.ACTIVE
        target.save(update_fields=['status', 'updated_at'])
    return target
