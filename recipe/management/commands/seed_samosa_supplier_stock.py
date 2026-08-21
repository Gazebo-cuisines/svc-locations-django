"""
Map Gazebo supplier + goods-in for samosa demo RMs/packaging; optional production_run E2E.

Usage:
  python manage.py seed_samosa_supplier_stock
  python manage.py seed_samosa_supplier_stock --map-only
  python manage.py seed_samosa_supplier_stock --goods-in-only
  python manage.py seed_samosa_supplier_stock --production-e2e
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from locations.models import Location, LocationRole
from planning.models import Resource
from product.models import Product, ProductSupplier, Unit
from recipe.management.commands.seed_demo_recipe_samosa import (
    INTERMEDIATES,
    LOC_RM_SRC,
    RAW_MATERIALS,
    RECIPE_SPECS,
)
from recipe.models import RecipeComponent, RecipeVersion, RecipeVersionStatus
from stock_ledger.models import (
    StockBalance,
    StockLot,
    StockLotOrigin,
)
from stock_ledger.util import services
from stock_ledger.util.conversions import StockValidationError

PACKS = Decimal('10')
OUTER_QTY = Decimal('1')
INNER_QTY = Decimal('10')


def _unit_by_names(*names: str) -> Unit:
    for name in names:
        unit = Unit.objects.filter(name__iexact=name).first()
        if unit is not None:
            return unit
    raise CommandError(
        f'Required unit not found (looked for {names}). '
        'Do not create units — add in DB first.'
    )


def _norm(name: str) -> str:
    return (name or '').strip().lower()


def _unit_family(unit: Unit) -> str:
    n = _norm(unit.name)
    if n in ('g', 'gram', 'grams'):
        return 'mass_g'
    if n in ('kg', 'kilogram', 'kilograms'):
        return 'mass_kg'
    if n in ('liter', 'litre', 'liters', 'litres', 'l'):
        return 'liter'
    if n in ('each', 'ea', 'unit'):
        return 'each'
    return 'other'


class Command(BaseCommand):
    help = (
        'Gazebo supplier map (1x10) + goods-in for samosa demo RMs; '
        'optional production_run E2E'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--map-only',
            action='store_true',
            help='Only create ProductSupplier rows',
        )
        parser.add_argument(
            '--goods-in-only',
            action='store_true',
            help='Only goods-in (creates missing mappings first)',
        )
        parser.add_argument(
            '--skip-map',
            action='store_true',
            help='With --goods-in-only, do not create mappings',
        )
        parser.add_argument(
            '--production-e2e',
            action='store_true',
            help='After stock, walk production_run for 910001–910006',
        )
        parser.add_argument(
            '--e2e-only',
            action='store_true',
            help='Only production E2E (skip map and goods-in)',
        )
        parser.add_argument(
            '--packs',
            type=str,
            default='10',
            help='Received pack count per product (default 10)',
        )

    def handle(self, *args, **options):
        packs = Decimal(str(options['packs']))
        if packs <= 0:
            raise CommandError('--packs must be positive')

        map_only = options['map_only']
        goods_in_only = options['goods_in_only']
        skip_map = options['skip_map']
        do_e2e = options['production_e2e'] or options['e2e_only']

        if map_only and goods_in_only:
            raise CommandError('Use only one of --map-only / --goods-in-only')

        do_map = True
        do_goods = True
        if options['e2e_only']:
            do_map = False
            do_goods = False
        elif map_only:
            do_goods = False
        elif goods_in_only:
            do_map = not skip_map

        if not do_map and not do_goods and not do_e2e:
            raise CommandError('Nothing to do')

        supplier = self._resolve_gazebo_supplier()
        units = self._resolve_shape_units()
        liter_msg = (
            f' liter={units["liter"].id}' if units.get('liter') else ' liter=(none)'
        )
        self.stdout.write(
            f'supplier id={supplier.id} name={supplier.name!r} '
            f'box={units["box"].id} kg={units["kg"].id} each={units["each"].id}'
            f'{liter_msg}'
        )

        rm_ids = [row[0] for row in RAW_MATERIALS]
        # Stock ledger rejects inactive products; demo rows may have been soft-deactivated.
        reactivated = Product.objects.filter(pk__in=rm_ids, is_active=False).update(
            is_active=True,
        )
        if reactivated:
            self.stdout.write(
                self.style.WARNING(f'reactivated {reactivated} demo RM/packaging products')
            )

        products = list(
            Product.objects.filter(pk__in=rm_ids)
            .select_related('unit')
            .order_by('id')
        )
        if len(products) != len(RAW_MATERIALS):
            found = {p.id for p in products}
            missing = [row[0] for row in RAW_MATERIALS if row[0] not in found]
            raise CommandError(
                f'Missing samosa demo products {missing}. '
                'Run: python manage.py seed_demo_recipe_samosa --raw-materials-only'
            )

        mappings: dict[int, ProductSupplier] = {}
        if do_map:
            for product in products:
                row = self._ensure_mapping(product, supplier, units)
                mappings[product.id] = row
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  map product={product.id} {row.shape_format_label} '
                        f'default={row.is_default}'
                    )
                )
        else:
            for product in products:
                row = (
                    ProductSupplier.objects
                    .filter(product=product, supplier=supplier, is_default=True)
                    .select_related('inner_unit', 'outer_unit')
                    .first()
                )
                if row is None:
                    row = (
                        ProductSupplier.objects
                        .filter(product=product, supplier=supplier)
                        .select_related('inner_unit', 'outer_unit')
                        .first()
                    )
                if row is None:
                    raise CommandError(
                        f'No Gazebo mapping for product {product.id}; '
                        'run without --skip-map'
                    )
                mappings[product.id] = row

        if do_goods:
            if not Location.objects.filter(pk=LOC_RM_SRC).exists():
                raise CommandError(
                    f'RM store location {LOC_RM_SRC} missing. '
                    'Run seed_demo_recipe_samosa --locations-only'
                )
            tag = uuid4().hex[:8]
            for product in products:
                mapping = mappings[product.id]
                qty = self._receipt_qty(product, mapping, packs)
                entry = self._goods_in(product, qty, tag)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  goods-in product={product.id} qty={qty} '
                        f'unit={product.unit.name} entry={entry.id} '
                        f'loc={LOC_RM_SRC}'
                    )
                )

        if do_e2e:
            self._run_production_e2e()

        self.stdout.write(self.style.SUCCESS('Done.'))

    def _resolve_gazebo_supplier(self) -> Location:
        qs = (
            Location.objects
            .filter(roles__role=LocationRole.SUPPLIER, name__icontains='gazebo')
            .distinct()
            .order_by('id')
        )
        supplier = qs.first()
        if supplier is None:
            raise CommandError(
                'No supplier location named Gazebo found. '
                'Create/import it first (role=supplier).'
            )
        if qs.count() > 1:
            self.stdout.write(
                self.style.WARNING(
                    f'Multiple Gazebo suppliers; using id={supplier.id} '
                    f'name={supplier.name!r}'
                )
            )
        return supplier

    def _resolve_shape_units(self) -> dict[str, Unit]:
        units = {
            'box': _unit_by_names('Box', 'box'),
            'kg': _unit_by_names('Kg', 'KG', 'kg', 'Kilogram'),
            'each': _unit_by_names('Each', 'each', 'EA'),
        }
        liter = Unit.objects.filter(name__iexact='Liter').first()
        if liter is None:
            liter = Unit.objects.filter(name__iexact='Litre').first()
        if liter is not None:
            units['liter'] = liter
        return units

    def _shape_for_product(
        self, product: Product, units: dict[str, Unit],
    ) -> tuple[Unit, Unit]:
        family = _unit_family(product.unit)
        if family in ('mass_g', 'mass_kg'):
            return units['box'], units['kg']
        if family == 'liter':
            if 'liter' not in units:
                raise CommandError(
                    f'Product {product.id} is Liter but no Liter unit exists in DB'
                )
            return units['box'], units['liter']
        if family == 'each':
            return units['box'], units['each']
        raise CommandError(
            f'Unsupported stock unit {product.unit.name!r} on product {product.id}'
        )

    def _ensure_mapping(
        self,
        product: Product,
        supplier: Location,
        units: dict[str, Unit],
    ) -> ProductSupplier:
        code = f'DEMO-{product.id}'
        existing = ProductSupplier.objects.filter(
            product=product, supplier=supplier, supplier_code=code,
        ).select_related('outer_unit', 'inner_unit').first()
        if existing is not None:
            return existing

        outer_unit, inner_unit = self._shape_for_product(product, units)
        with transaction.atomic():
            ProductSupplier.objects.filter(
                product=product, is_default=True,
            ).update(is_default=False)
            row = ProductSupplier(
                product=product,
                supplier=supplier,
                supplier_code=code,
                supplier_product_name=product.name[:128],
                outer_qty=OUTER_QTY,
                outer_unit=outer_unit,
                inner_qty=INNER_QTY,
                inner_unit=inner_unit,
                is_default=True,
                is_active=True,
            )
            row.save()
        return row

    def _receipt_qty(
        self,
        product: Product,
        mapping: ProductSupplier,
        packs: Decimal,
    ) -> Decimal:
        base = (packs * mapping.multiplier).quantize(Decimal('0.000001'))
        stock = _unit_family(product.unit)
        inner = _unit_family(mapping.inner_unit)
        if stock == 'mass_g' and inner == 'mass_kg':
            return (base * Decimal('1000')).quantize(Decimal('0.000001'))
        if stock == inner:
            return base
        if stock == 'mass_kg' and inner == 'mass_kg':
            return base
        raise CommandError(
            f'Cannot convert receipt for product {product.id}: '
            f'stock={product.unit.name} inner={mapping.inner_unit.name}'
        )

    def _goods_in(self, product: Product, quantity: Decimal, tag: str):
        use_by = timezone.now().date() + timedelta(days=365)
        lot = StockLot.objects.create(
            product=product,
            trace_number=f'SAM-GI-{product.id}-{tag}',
            origin=StockLotOrigin.PURCHASE,
            production_date=timezone.now().date(),
            use_by=use_by,
            supplier_lot_code=f'GAZEBO-{product.id}',
        )
        return services.receipt(
            idempotency_key=f'samosa-gi-{product.id}-{tag}',
            lot=lot,
            location_id=LOC_RM_SRC,
            quantity=quantity,
            unit_id=product.unit_id,
            effective_at=timezone.now(),
        )

    def _run_production_e2e(self) -> None:
        intermediate_ids = [row[0] for row in INTERMEDIATES]
        n = Product.objects.filter(pk__in=intermediate_ids, is_active=False).update(
            is_active=True,
        )
        if n:
            self.stdout.write(
                self.style.WARNING(f'reactivated {n} intermediate/FG products')
            )
        resource = Resource.objects.filter(is_active=True).order_by('id').first()
        if resource is None:
            raise CommandError(
                'No active planning.Resource — required for production_run'
            )

        dest_by_product = {row[0]: row[4] for row in INTERMEDIATES}
        tag = uuid4().hex[:8]
        base_date = timezone.now().date()
        self.stdout.write(f'production_e2e resource={resource.id} tag={tag}')

        for parent_id, make_loc, _batch, _ukey, _comps in RECIPE_SPECS:
            product = Product.objects.select_related('unit').get(pk=parent_id)
            version = (
                RecipeVersion.objects
                .filter(
                    recipe__product_id=parent_id,
                    status=RecipeVersionStatus.ACTIVE,
                )
                .order_by('-version_number')
                .first()
            )
            if version is None:
                raise CommandError(
                    f'No ACTIVE recipe for product {parent_id}. '
                    'Run seed_demo_recipe_samosa --recipes-only'
                )

            made = self._scaled_made(parent_id, make_loc, version)
            if made <= 0:
                raise CommandError(
                    f'Stage {parent_id}: cannot scale made qty (no component stock)'
                )
            self.stdout.write(f'  stage {parent_id} made={made}')
            out_loc = dest_by_product.get(parent_id, make_loc)
            use_by = base_date + timedelta(days=30)
            lot = StockLot.objects.create(
                product=product,
                trace_number=f'SAM-PR-{parent_id}-{tag}',
                origin=StockLotOrigin.PRODUCTION,
                production_date=base_date,
                use_by=use_by,
                recipe_version=version,
            )
            try:
                entry, _run = services.production_output(
                    idempotency_key=f'samosa-prod-{parent_id}-{tag}',
                    lot=lot,
                    location_id=out_loc,
                    quantity=made,
                    resource_id=resource.id,
                    base_date=base_date,
                    unit_id=product.unit_id,
                    counterparty_location_id=make_loc,
                    effective_at=timezone.now(),
                )
            except StockValidationError as exc:
                raise CommandError(f'production_output {parent_id}: {exc}') from exc

            req = services.production_requirements(
                output_entry_id=entry.id,
                location_id=None,
            )
            for line in req['components']:
                needed = Decimal(line['remaining_quantity'])
                if needed <= 0:
                    continue
                comp_id = line['component_product_id']
                bal = self._find_balance(comp_id, prefer_location_id=make_loc)
                if bal is None:
                    raise CommandError(
                        f'Stage {parent_id}: no stock for component {comp_id} '
                        f'(need {needed})'
                    )
                if bal.quantity < needed:
                    raise CommandError(
                        f'Stage {parent_id}: short component {comp_id} '
                        f'have={bal.quantity} need={needed} loc={bal.location_id}'
                    )
                if bal.location_id != make_loc:
                    try:
                        services.transfer(
                            idempotency_key=(
                                f'samosa-xfer-{parent_id}-{comp_id}-{tag}'
                            ),
                            lot=bal.lot,
                            from_location_id=bal.location_id,
                            to_location_id=make_loc,
                            quantity=needed,
                            unit_id=bal.lot.product.unit_id,
                            effective_at=timezone.now(),
                        )
                    except StockValidationError as exc:
                        raise CommandError(
                            f'transfer {comp_id}→{make_loc}: {exc}'
                        ) from exc
                    consume_loc = make_loc
                    consume_lot = bal.lot
                else:
                    consume_loc = bal.location_id
                    consume_lot = bal.lot

                try:
                    services.production_consume(
                        idempotency_key=(
                            f'samosa-consume-{entry.id}-{comp_id}-{tag}'
                        ),
                        output_entry_id=entry.id,
                        lot=consume_lot,
                        location_id=consume_loc,
                        quantity=needed,
                        unit_id=consume_lot.product.unit_id,
                        effective_at=timezone.now(),
                    )
                except StockValidationError as exc:
                    raise CommandError(
                        f'consume {comp_id} for {parent_id}: {exc}'
                    ) from exc

            self.stdout.write(
                self.style.SUCCESS(
                    f'  produced product={parent_id} qty={made} '
                    f'entry={entry.id} loc={out_loc}'
                )
            )

    def _scaled_made(
        self,
        parent_id: int,
        make_loc: int,
        version: RecipeVersion,
    ) -> Decimal:
        """Cap made=1 by available component stock (requirements = made * qty / yield)."""
        process_loss = version.process_loss or Decimal('1')
        if process_loss <= 0:
            process_loss = Decimal('1')
        components = (
            RecipeComponent.objects
            .filter(recipe_version_id=version.id)
            .select_related('component_product__yield_data')
        )
        scale = Decimal('1')
        for comp in components:
            yf = Decimal('1')
            try:
                factor = comp.component_product.yield_data.yield_factor
                if factor is not None and factor > 0:
                    yf = factor
            except ObjectDoesNotExist:
                pass
            need_per = (comp.quantity / process_loss / yf).quantize(Decimal('0.000001'))
            if need_per <= 0:
                continue
            bal = self._find_balance(
                comp.component_product_id, prefer_location_id=make_loc,
            )
            avail = bal.quantity if bal is not None else Decimal('0')
            scale = min(scale, (avail / need_per).quantize(Decimal('0.000001')))
        return max(scale, Decimal('0'))

    def _find_balance(
        self,
        product_id: int,
        prefer_location_id: int,
    ) -> StockBalance | None:
        preferred = (
            StockBalance.objects
            .filter(
                lot__product_id=product_id,
                location_id=prefer_location_id,
                quantity__gt=0,
            )
            .select_related('lot', 'lot__product')
            .order_by('-quantity')
            .first()
        )
        if preferred is not None:
            return preferred
        return (
            StockBalance.objects
            .filter(lot__product_id=product_id, quantity__gt=0)
            .select_related('lot', 'lot__product')
            .order_by('-quantity')
            .first()
        )
