from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

CONTRACT_VERSION = 'planning-adapters-1.0'


@dataclass(frozen=True)
class ProductSpec:
    id: int
    name: str
    unit_id: int
    source_location_id: int
    destination_location_id: int
    yield_factor: Decimal
    process_loss: Decimal  # from active/pinned recipe version when known; else 1
    full_batches_only: bool
    align_unitary_weight: bool
    standard_batch_kg: Optional[Decimal]
    consider_stock_in_plan: bool
    min_stock: Optional[Decimal]
    max_stock: Optional[Decimal]
    absolute_min_shelf_life_days: int
    default_resource_id: Optional[int]


@dataclass(frozen=True)
class ComponentSpec:
    product_id: int
    quantity: Decimal
    unit_id: int
    line_no: int


@dataclass(frozen=True)
class RecipeVersionSpec:
    version_id: int
    product_id: int
    recipe_code: str
    version_number: int
    process_loss: Decimal
    batch_quantity: Optional[Decimal]
    process_batch: bool
    components: tuple[ComponentSpec, ...]


@dataclass(frozen=True)
class LotSpec:
    lot_id: int
    product_id: int
    quantity_on_hand: Decimal
    location_id: int
    use_by: Optional[date]
    production_date: Optional[date]
    atp: Decimal
    trace_number: Optional[str] = None


@dataclass(frozen=True)
class SupplySpec:
    id: int
    product_id: int
    location_id: int
    expected_at: datetime
    quantity: Decimal
