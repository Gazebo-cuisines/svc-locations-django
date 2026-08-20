"""CVSAL-G12T — Gazebo G&G Vegetable Samosa 100G × 12.

Pedro 1240 → CVSAL × 12 + OC003 box. No outer sleeve in Pedro tree.
Uses existing CVSAL packed item (created in CVSAL-1G6T pilot).

  python manage.py import_live_cvsal_g12t
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe
from decimal import Decimal

from ._live202_helpers import (
    DOCS, STAMP, U_UNIT, U_G, U_TRAY, LOC, CLASS_SNACK,
    make_product, make_draft, add_line, clear_lines, attach_file, require_products,
)

STOCK_CODE = 'CVSAL-G12T'
NOTE_FG = (
    'Gazebo G&G Vegetable Samosa 12-pack. '
    'Outer case is OC003 (SK45×6). No outer sleeve in Pedro tree. '
    'Pedro source: id 1240.'
)
CAT_FG = 127   # Cased Items


class Command(BaseCommand):
    help = f'Create {STOCK_CODE} draft recipe. Never activates.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products('CVSAL', 'OC003')
            cvsal = needed['CVSAL']
            oc003 = needed['OC003']

            fg, created = make_product(
                STOCK_CODE,
                'Gazebo - G&G - Vegetable Samosa | 100G X 12',
                CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'],
                None, True, NOTE_FG,
            )
            self.stdout.write(f"{'NEW' if created else 'reuse'} {STOCK_CODE} id={fg.id}")

            ProductPackaging.objects.update_or_create(
                product_id=fg.id,
                defaults={'items_per_unit': Decimal('12'), 'unitary_weight': Decimal('100'),
                          'pack_weight': Decimal('1200')},
            )
            ProductShelfLife.objects.get_or_create(product_id=fg.id, defaults={'shelf_life_days': 14})
            flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
            if not flags.is_sales_item:
                flags.is_sales_item = True
                flags.has_plan = True
                flags.save(update_fields=['is_sales_item', 'has_plan'])

            v = make_draft(fg, NOTE_FG)
            clear_lines(v)
            add_line(v, 1, cvsal, 12, U_UNIT)
            add_line(v, 2, oc003, 1, U_UNIT)

            sync_has_recipe(fg.id)

        self.stdout.write(self.style.SUCCESS(f'draft fg={v.id}'))
