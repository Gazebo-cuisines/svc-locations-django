from __future__ import annotations

from locations.models import LocationStockProfile

from planning.adapters.types import CONTRACT_VERSION, ProductSpec

__all__ = ['CONTRACT_VERSION', 'plannable_locations', 'location_min_shelf_life']


def plannable_locations(product: ProductSpec) -> list[int]:
    """Locations whose stock counts for planning ATP.

    Prefer product source container when it has real_stock; otherwise all
    real_stock locations (stable sorted ids).
    """
    real_ids = list(
        LocationStockProfile.objects
        .filter(real_stock=True)
        .values_list('location_id', flat=True)
    )
    if not real_ids:
        return [product.source_location_id]

    if product.source_location_id in real_ids:
        # Source first, then other real-stock sites
        others = [i for i in sorted(real_ids) if i != product.source_location_id]
        return [product.source_location_id, *others]
    return sorted(real_ids)


def location_min_shelf_life(location_id: int | None) -> int:
    if location_id is None:
        return 0
    profile = (
        LocationStockProfile.objects
        .filter(location_id=location_id)
        .values_list('min_shelf_life', flat=True)
        .first()
    )
    if profile is None:
        return 0
    return int(profile)
