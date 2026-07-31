from __future__ import annotations

from decimal import Decimal

from product.models import Product
from recipe.models import Recipe, RecipeVersion, RecipeVersionStatus

from planning.adapters.types import CONTRACT_VERSION, ProductSpec

__all__ = ['CONTRACT_VERSION', 'get_product_spec']


def get_product_spec(product_id: int, *, process_loss: Decimal | None = None) -> ProductSpec:
    product = (
        Product.objects
        .select_related(
            'flags',
            'yield_data',
            'packaging',
            'stock_policy',
            'shelf_life',
            'production',
        )
        .get(pk=product_id)
    )
    flags = getattr(product, 'flags', None)
    yield_data = getattr(product, 'yield_data', None)
    packaging = getattr(product, 'packaging', None)
    stock_policy = getattr(product, 'stock_policy', None)
    shelf_life = getattr(product, 'shelf_life', None)
    production = getattr(product, 'production', None)

    loss = process_loss
    if loss is None:
        loss = Decimal('1')
        recipe = Recipe.objects.filter(product_id=product_id).first()
        if recipe is not None:
            active = (
                RecipeVersion.objects
                .filter(recipe_id=recipe.id, status=RecipeVersionStatus.ACTIVE)
                .order_by('-version_number')
                .first()
            )
            if active is not None:
                loss = active.process_loss

    abs_min = 0
    if shelf_life and shelf_life.absolute_min_shelf_life_days is not None:
        abs_min = int(shelf_life.absolute_min_shelf_life_days)

    return ProductSpec(
        id=product.id,
        name=product.name,
        unit_id=product.unit_id,
        source_location_id=product.source_container_id,
        destination_location_id=product.destination_container_id,
        yield_factor=(
            yield_data.yield_factor
            if yield_data is not None
            else Decimal('1')
        ),
        process_loss=loss if loss > 0 else Decimal('1'),
        full_batches_only=bool(flags and flags.full_batches_only),
        align_unitary_weight=bool(packaging and packaging.align_unitary_weight),
        standard_batch_kg=(
            packaging.gross_unitary_weight
            if packaging is not None
            else None
        ),
        consider_stock_in_plan=bool(flags and flags.consider_stock_in_plan),
        min_stock=stock_policy.min_stock if stock_policy else None,
        max_stock=stock_policy.max_stock if stock_policy else None,
        absolute_min_shelf_life_days=abs_min,
        default_resource_id=(
            production.default_resource_id if production else None
        ),
    )
