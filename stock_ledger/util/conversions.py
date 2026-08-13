from decimal import Decimal

from product.models import Product, ProductPackaging, ProductSupplier, Unit

from stock_ledger.models import StockUnitConversion


class StockValidationError(ValueError):
    """Raised when a stock ledger rule is violated."""


GLOBAL_UNIT_TO_KG = {
    'grams': Decimal('0.001000'),
    'Kg': Decimal('1.000000'),
}

PRODUCT_SPECIFIC_UNIT_NAMES = frozenset({'unit', 'Box', 'Liter'})


def resolve_to_kg(*, unit_id: int, product_id: int | None = None) -> Decimal:
    """
    Return kg factor for unit (+ optional product override).
    Raises StockValidationError if no factor — never defaults to 1.
    """
    if product_id is not None:
        row = (
            StockUnitConversion.objects
            .filter(unit_id=unit_id, product_id=product_id)
            .only('to_kg')
            .first()
        )
        if row is not None:
            return row.to_kg

    row = (
        StockUnitConversion.objects
        .filter(unit_id=unit_id, product__isnull=True)
        .only('to_kg')
        .first()
    )
    if row is not None:
        return row.to_kg

    raise StockValidationError(
        f'No stock_unit_conversion for unit_id={unit_id}'
        + (f', product_id={product_id}' if product_id is not None else '')
    )


def seed_global_unit_conversions() -> int:
    """Upsert global grams/Kg rules. Returns number of rows upserted."""
    written = 0
    for name, to_kg in GLOBAL_UNIT_TO_KG.items():
        try:
            unit = Unit.objects.get(name=name)
        except Unit.DoesNotExist:
            continue
        StockUnitConversion.objects.update_or_create(
            unit_id=unit.id,
            product=None,
            defaults={'to_kg': to_kg, 'source': 'global'},
        )
        written += 1
    return written


def sync_product_unit_conversions_from_packaging() -> int:
    """
    Upsert product-specific unit/Box/Liter factors from packaging.unitary_weight.
    Skips rows with missing/non-positive weight. Returns upserts count.
    """
    units = {
        u.name: u
        for u in Unit.objects.filter(name__in=PRODUCT_SPECIFIC_UNIT_NAMES)
    }
    if not units:
        return 0

    written = 0
    qs = (
        ProductPackaging.objects
        .exclude(unitary_weight__isnull=True)
        .filter(unitary_weight__gt=0)
        .select_related('product')
    )
    for packaging in qs:
        if packaging.product.is_downtime:
            continue
        for name, unit in units.items():
            StockUnitConversion.objects.update_or_create(
                unit_id=unit.id,
                product_id=packaging.product_id,
                defaults={
                    'to_kg': packaging.unitary_weight,
                    'source': 'product_packaging',
                },
            )
            written += 1
    return written


Q6 = Decimal('0.000001')


def to_product_unit(
    qty: Decimal,
    from_unit_id: int,
    product: Product,
) -> Decimal:
    """Convert qty in from_unit into product.unit."""
    dest_id = product.unit_id
    if dest_id is None:
        raise StockValidationError(
            f'product_id={product.id} has no stock unit',
        )
    qty = Decimal(qty)
    if from_unit_id == dest_id:
        return qty.quantize(Q6)
    src = resolve_to_kg(unit_id=from_unit_id, product_id=product.id)
    dest = resolve_to_kg(unit_id=dest_id, product_id=product.id)
    if dest == 0:
        raise StockValidationError(
            f'product_id={product.id} unit_id={dest_id} has to_kg=0',
        )
    return (qty * src / dest).quantize(Q6)


def packs_to_stock(
    pack_count: Decimal,
    mapping: ProductSupplier,
    product: Product,
) -> Decimal:
    """N packs of this supplier shape → quantity in product.unit."""
    return to_product_unit(
        Decimal(pack_count) * mapping.multiplier,
        mapping.inner_unit_id,
        product,
    )


def stock_to_packs(
    stock_qty: Decimal,
    mapping: ProductSupplier,
    product: Product,
) -> Decimal:
    per_pack = packs_to_stock(Decimal('1'), mapping, product)
    if per_pack == 0:
        raise StockValidationError('pack size is 0')
    return (Decimal(stock_qty) / per_pack).quantize(Q6)


def stock_to_kg(stock_qty: Decimal, product: Product) -> Decimal | None:
    """Warehouse KG column. None if product.unit has no kg conversion."""
    if product.unit_id is None:
        return None
    try:
        factor = resolve_to_kg(unit_id=product.unit_id, product_id=product.id)
    except StockValidationError:
        return None
    return (Decimal(stock_qty) * factor).quantize(Q6)
