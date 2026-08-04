"""
Seed demo data for Potato & Pea Samosa BOM.

Usage:
  python manage.py seed_demo_recipe_samosa --raw-materials-only
  python manage.py seed_demo_recipe_samosa --locations-only
  python manage.py seed_demo_recipe_samosa --intermediates-only
  python manage.py seed_demo_recipe_samosa --recipes-only
  ... --flush with any of the above
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductFlags,
    ProductYield,
    Range,
    Unit,
)
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from recipe.utils import activate_version, sync_has_recipe

LOC_RM_SRC = 910200

LOOKUP_CLASS = 9101
LOOKUP_CATEGORY = 9101
LOOKUP_RANGE = 9101
LOOKUP_CLASS_FG = 9102
UNIT_G = 9101
UNIT_EACH = 9102

CHAIN_LOCATIONS = (
    (910201, 'Spice Room', 'SAM-SPICE'),
    (910202, 'Mixers', 'SAM-MIX'),
    (910203, 'Belts', 'SAM-BELT'),
    (910204, 'Fryers', 'SAM-FRY'),
    (910205, 'High Risk', 'SAM-HR'),
    (910206, 'Sleeving', 'SAM-SLEEVE'),
    (910207, 'Dispatch', 'SAM-DISP'),
)
CHAIN_LOC_IDS = [row[0] for row in CHAIN_LOCATIONS]

# id, name, recipe_code, src_loc_id, dst_loc_id, unit_key
INTERMEDIATES = (
    (910001, 'Veg Samosa - 005 - Spice', 'GFF005R-S', 910201, 910202, 'g'),
    (910002, 'Veg Samosa - 005 - Mixer', 'GFF005R-Mx', 910202, 910203, 'g'),
    (910003, 'Veg Samosa - 100g - 005 - Belt', 'GFF005R-B-100', 910203, 910204, 'each'),
    (910004, 'Veg Samosa - 100g - 005 - Frying', 'GFF005R-F-100', 910204, 910205, 'each'),
    (
        910005,
        'GG - 1x Veg Samosa - 005R - 100g SQ Tray',
        'CVSAL',
        910205,
        910206,
        'each',
    ),
    (
        910006,
        'Gazebo - G&G - Potato & Pea Samosa | 100G X 6',
        'CVSAL-1G6T',
        910206,
        910207,
        'each',
    ),
)
INTERMEDIATE_IDS = [row[0] for row in INTERMEDIATES]

RAW_MATERIALS = (
    (910101, 'SUGAR', 'RM-SUGAR', 'g', Decimal('1.0000')),
    (910102, 'CUMIN SEED', 'RM-CUMIN', 'g', Decimal('1.0000')),
    (910103, 'CHILLI POWDER', 'RM-CHILLI', 'g', Decimal('1.0000')),
    (910104, 'VEG SAMOSA SEASONING', 'RM-VSAM-SEAS', 'g', Decimal('1.0000')),
    (910105, 'POTATO DICED 10MM (FROZEN)', 'RM-POT-DICE', 'g', Decimal('1.0000')),
    (910106, 'POTATO MASH / FLAKE', 'RM-POT-MASH', 'g', Decimal('1.0000')),
    (910107, 'ONION WHITE DICED 10MM (FROZEN)', 'RM-ONION', 'g', Decimal('1.0000')),
    (910108, 'PEAS (FROZEN)', 'RM-PEAS', 'g', Decimal('1.0000')),
    (910109, 'Water - Step 1', 'RM-WATER-S1', 'g', Decimal('1.0000')),
    (910110, 'LEMON JUICE', 'RM-LEMON', 'g', Decimal('1.0000')),
    (910111, 'SAMOSA PASTRY - LARGE CUT', 'RM-PASTRY', 'each', Decimal('1.0000')),
    (910112, 'Tray - Grab & Go - Square Tray', 'PK-TRAY-GG', 'each', Decimal('0.9900')),
    (910113, 'Film - K peel 7G - 25mu - f240mm', 'PK-FILM-K7', 'each', Decimal('1.0000')),
    (
        910114,
        'G&G Potato & Pea Samosa sleeve label (95x263mm)',
        'PK-SLEEVE-VSAM',
        'each',
        Decimal('0.9999'),
    ),
    (
        910115,
        'Box - Grab and Go x 6 Tubs - 280x148x134',
        'PK-BOX-GG6',
        'each',
        Decimal('0.9900'),
    ),
)
RM_IDS = [row[0] for row in RAW_MATERIALS]

# parent_product_id, make_location_id, batch_qty, batch_unit_key, components[(comp_id, qty, unit_key)]
RECIPE_SPECS = (
    (
        910001,
        910201,
        Decimal('13106.000000'),
        'g',
        (
            (910101, Decimal('92.477000'), 'g'),
            (910102, Decimal('38.532000'), 'g'),
            (910103, Decimal('28.918000'), 'g'),
            (910104, Decimal('840.073000'), 'g'),
        ),
    ),
    (
        910002,
        910202,
        Decimal('215606.000000'),
        'g',
        (
            (910105, Decimal('50000.000000'), 'g'),
            (910106, Decimal('25000.000000'), 'g'),
            (910107, Decimal('50000.000000'), 'g'),
            (910108, Decimal('50000.000000'), 'g'),
            (910109, Decimal('20000.000000'), 'g'),
            (910110, Decimal('7500.000000'), 'g'),
            (910001, Decimal('13106.000000'), 'g'),
        ),
    ),
    (
        910003,
        910203,
        None,
        'each',
        (
            (910002, Decimal('69.550300'), 'g'),
            (910111, Decimal('1.000000'), 'each'),
        ),
    ),
    (
        910004,
        910204,
        None,
        'each',
        ((910003, Decimal('1.000000'), 'each'),),
    ),
    (
        910005,
        910205,
        None,
        'each',
        (
            (910004, Decimal('1.000000'), 'each'),
            (910112, Decimal('1.000000'), 'each'),
            (910113, Decimal('0.200000'), 'each'),
        ),
    ),
    (
        910006,
        910206,
        None,
        'each',
        (
            (910005, Decimal('6.000000'), 'each'),
            (910114, Decimal('6.000000'), 'each'),
            (910115, Decimal('1.000000'), 'each'),
        ),
    ),
)
RECIPE_PRODUCT_IDS = [row[0] for row in RECIPE_SPECS]


class Command(BaseCommand):
    help = 'Seed samosa demo raw materials / locations / intermediates / recipes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--raw-materials-only',
            action='store_true',
            help='Create only raw materials + packaging (chunk 1)',
        )
        parser.add_argument(
            '--locations-only',
            action='store_true',
            help='Create only make/dispatch locations (chunk 2)',
        )
        parser.add_argument(
            '--intermediates-only',
            action='store_true',
            help='Create only intermediate + FG products (chunk 3)',
        )
        parser.add_argument(
            '--recipes-only',
            action='store_true',
            help='Create recipes/versions/components and activate (chunk 4)',
        )
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Flush the selected chunk demo rows first',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['locations_only']:
            self._seed_locations(flush=options['flush'])
            return
        if options['raw_materials_only']:
            self._seed_raw_materials(flush=options['flush'])
            return
        if options['intermediates_only']:
            self._seed_intermediates(flush=options['flush'])
            return
        if options['recipes_only']:
            self._seed_recipes(flush=options['flush'])
            return
        self.stdout.write(
            self.style.ERROR(
                'Pass --raw-materials-only, --locations-only, '
                '--intermediates-only, or --recipes-only.',
            ),
        )

    def _seed_recipes(self, *, flush: bool):
        missing = [
            pid
            for pid in RECIPE_PRODUCT_IDS
            if not Product.objects.filter(pk=pid).exists()
        ]
        if missing:
            self.stdout.write(
                self.style.ERROR(
                    f'Missing products {missing}. Run --intermediates-only first.',
                ),
            )
            return

        if flush:
            Recipe.objects.filter(product_id__in=RECIPE_PRODUCT_IDS).delete()
            self.stdout.write('Flushed demo recipes for 910001–910006.')

        lookups = self._ensure_lookups()
        created = 0
        skipped = 0

        for (
            parent_id,
            location_id,
            batch_qty,
            batch_unit_key,
            components,
        ) in RECIPE_SPECS:
            if Recipe.objects.filter(product_id=parent_id).exists():
                skipped += 1
                self.stdout.write(f'  skip existing recipe product={parent_id}')
                continue

            batch_unit = (
                lookups['unit_g'] if batch_unit_key == 'g' else lookups['unit_each']
            )
            recipe = Recipe.objects.create(
                product_id=parent_id,
                name=Product.objects.get(pk=parent_id).name,
                remarks='Demo recipe from mock-data/recipe.md (chunk 4).',
            )
            version = RecipeVersion.objects.create(
                recipe=recipe,
                version_number=1,
                status=RecipeVersionStatus.DRAFT,
                process_loss=Decimal('1.0000'),
                batch_quantity=batch_qty,
                batch_unit=batch_unit,
                sum_batch_quantity=batch_qty,
                location_id=location_id,
            )
            for line_no, (comp_id, qty, unit_key) in enumerate(components, start=1):
                unit = lookups['unit_g'] if unit_key == 'g' else lookups['unit_each']
                RecipeComponent.objects.create(
                    recipe_version=version,
                    line_no=line_no,
                    component_product_id=comp_id,
                    quantity=qty,
                    unit=unit,
                    batch_quantity=qty,
                )
            activate_version(version)
            sync_has_recipe(parent_id)
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  recipe product={parent_id} lines={len(components)} ACTIVE',
                ),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Chunk 4 done: created={created} skipped={skipped}.',
            ),
        )

    def _seed_intermediates(self, *, flush: bool):
        missing_locs = [
            loc_id
            for loc_id in CHAIN_LOC_IDS
            if not Location.objects.filter(pk=loc_id).exists()
        ]
        if missing_locs:
            self.stdout.write(
                self.style.ERROR(
                    f'Missing locations {missing_locs}. Run --locations-only first.',
                ),
            )
            return

        if flush:
            deleted, _ = Product.objects.filter(pk__in=INTERMEDIATE_IDS).delete()
            self.stdout.write(f'Flushed intermediate/FG products ({deleted} rows).')

        lookups = self._ensure_lookups()
        fg_class, _ = ProductClass.objects.get_or_create(
            id=LOOKUP_CLASS_FG,
            defaults={'name': 'Finished / Intermediate'},
        )
        created = 0
        skipped = 0

        for (
            product_id,
            name,
            recipe_code,
            src_id,
            dst_id,
            unit_key,
        ) in INTERMEDIATES:
            if Product.objects.filter(pk=product_id).exists():
                skipped += 1
                self.stdout.write(f'  skip existing id={product_id} {name}')
                continue

            unit = lookups['unit_g'] if unit_key == 'g' else lookups['unit_each']
            product = Product.objects.create(
                id=product_id,
                name=name,
                recipe_code=recipe_code,
                is_active=True,
                is_downtime=False,
                remarks='Demo intermediate/FG for Potato & Pea Samosa BOM (chunk 3).',
                product_class=fg_class,
                category=lookups['category'],
                range=lookups['range'],
                unit=unit,
                purchasing_unit=unit,
                source_container_id=src_id,
                destination_container_id=dst_id,
            )
            ProductYield.objects.create(
                product=product,
                yield_factor=Decimal('1.0000'),
                yield_factor_auto=Decimal('1.0000'),
            )
            ProductFlags.objects.create(
                product=product,
                has_recipe=False,
                is_sales_item=(product_id == 910006),
            )
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  created id={product_id} {recipe_code} '
                    f'{src_id}->{dst_id}',
                ),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Chunk 3 done: created={created} skipped={skipped} '
                f'(ids {INTERMEDIATE_IDS[0]}–{INTERMEDIATE_IDS[-1]}).',
            ),
        )

    def _seed_locations(self, *, flush: bool):
        if flush:
            deleted, _ = Location.objects.filter(pk__in=CHAIN_LOC_IDS).delete()
            self.stdout.write(f'Flushed chain locations ({deleted} rows).')

        created = 0
        updated = 0
        for loc_id, name, external_code in CHAIN_LOCATIONS:
            loc, was_created = Location.objects.get_or_create(
                id=loc_id,
                defaults={
                    'name': name,
                    'external_code': external_code,
                    'visible': True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  created id={loc_id} {name}'),
                )
                continue
            changed = False
            if loc.name != name:
                loc.name = name
                changed = True
            if loc.external_code != external_code:
                loc.external_code = external_code
                changed = True
            if changed:
                loc.save(update_fields=['name', 'external_code', 'updated_at'])
                updated += 1
                self.stdout.write(
                    self.style.WARNING(f'  updated id={loc_id} {name}'),
                )
            else:
                self.stdout.write(f'  skip existing id={loc_id} {name}')

        Location.objects.get_or_create(
            id=LOC_RM_SRC,
            defaults={
                'name': 'Demo Samosa RM Store',
                'external_code': 'SAM-RM-SRC',
                'visible': True,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Chunk 2 done: created={created} updated={updated} '
                f'(ids {CHAIN_LOC_IDS[0]}–{CHAIN_LOC_IDS[-1]}).',
            ),
        )

    def _seed_raw_materials(self, *, flush: bool):
        if flush:
            deleted, _ = Product.objects.filter(pk__in=RM_IDS).delete()
            self.stdout.write(f'Flushed demo RM/packaging products ({deleted} rows).')

        lookups = self._ensure_lookups()
        src, dst = self._ensure_rm_locations()
        created = 0
        skipped = 0

        for product_id, name, recipe_code, unit_key, yield_factor in RAW_MATERIALS:
            if Product.objects.filter(pk=product_id).exists():
                skipped += 1
                self.stdout.write(f'  skip existing id={product_id} {name}')
                continue

            unit = lookups['unit_g'] if unit_key == 'g' else lookups['unit_each']
            product = Product.objects.create(
                id=product_id,
                name=name,
                recipe_code=recipe_code,
                is_active=True,
                is_downtime=False,
                remarks='Demo RM/packaging for Potato & Pea Samosa BOM (chunk 1).',
                product_class=lookups['product_class'],
                category=lookups['category'],
                range=lookups['range'],
                unit=unit,
                purchasing_unit=unit,
                source_container=src,
                destination_container=dst,
            )
            ProductYield.objects.create(
                product=product,
                yield_factor=yield_factor,
                yield_factor_auto=yield_factor,
            )
            ProductFlags.objects.create(
                product=product,
                has_recipe=False,
                is_purchase_item=True,
            )
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  created id={product_id} {name} yield={yield_factor}',
                ),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Chunk 1 done: created={created} skipped={skipped} '
                f'(ids {RM_IDS[0]}–{RM_IDS[-1]}).',
            ),
        )

    def _ensure_rm_locations(self):
        src, _ = Location.objects.get_or_create(
            id=LOC_RM_SRC,
            defaults={
                'name': 'Demo Samosa RM Store',
                'external_code': 'SAM-RM-SRC',
                'visible': True,
            },
        )
        dst, _ = Location.objects.get_or_create(
            id=910201,
            defaults={
                'name': 'Spice Room',
                'external_code': 'SAM-SPICE',
                'visible': True,
            },
        )
        return src, dst

    def _ensure_lookups(self):
        product_class, _ = ProductClass.objects.get_or_create(
            id=LOOKUP_CLASS,
            defaults={'name': 'Raw Material'},
        )
        category, _ = Category.objects.get_or_create(
            id=LOOKUP_CATEGORY,
            defaults={'name': 'Samosa Demo'},
        )
        rng, _ = Range.objects.get_or_create(
            id=LOOKUP_RANGE,
            defaults={'name': 'Potato & Pea Samosa'},
        )
        unit_g, _ = Unit.objects.get_or_create(
            id=UNIT_G,
            defaults={'name': 'g'},
        )
        unit_each, _ = Unit.objects.get_or_create(
            id=UNIT_EACH,
            defaults={'name': 'Each'},
        )
        return {
            'product_class': product_class,
            'category': category,
            'range': rng,
            'unit_g': unit_g,
            'unit_each': unit_each,
        }
