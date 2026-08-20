"""Veg samosa FG variants — batch import (one run = this batch).

Creates new packed items (CVSAC, CVSAL10, CVSAL20, CVSAC20) as empty drafts
with user notes, then builds FG recipes from Pedro tree.

No PDF needed for FG packs — Pedro is the source for pack counts.

  python manage.py import_live_veg_samosa_batch
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    U_UNIT, U_G, U_TRAY, LOC,
    make_product, make_draft, add_line, clear_lines, require_products,
    stamp,
)

CAT_PACKED = 76
CAT_FG = 127
CAT_FG_BULK = 127

# ── packed item notes ──────────────────────────────────────────────────────────
NOTE_CVSAC = (
    'SK4 tray of 4 vegetable samosas (40g each, 160g total). '
    'Please add the belt/fry recipe lines using GFF003R-F-40 (or equivalent 40g fry product) '
    'plus an SK4 tray (PKLEE001-XX) once those catalogue codes are confirmed.'
)
NOTE_CVSAL10 = (
    '10 × 100g G&G vegetable samosas in a bulk tray (1000g). '
    'Please add the packing recipe lines from the relevant QAS once the bulk tray code is confirmed.'
)
NOTE_CVSAL20 = (
    '20 × 100g G&G vegetable samosas in a bulk tray (2000g). '
    'Please add the packing recipe lines from the relevant QAS once the bulk tray code is confirmed.'
)
NOTE_CVSAC20 = (
    '20 × 25g vegetable samosas in a bulk tray (500g). '
    'Please add the belt/fry and tray lines once the 25g process chain codes are confirmed.'
)

# ── FG notes ──────────────────────────────────────────────────────────────────
NOTE_CVSAC_R6T   = 'Gazebo 4-pack veg samosa (160g) × 6. Sleeve S775-07, box OC008. Pedro id 1280.'
NOTE_CVSAC_R6TEB = 'Booths 4-pack veg samosa (160g) × 6. Sleeve S666-06, box OC002. Pedro id 1248.'
NOTE_CVSAC_R6TTA = 'TA 4-pack veg samosa (160g) × 6. No sleeve/box in Pedro tree. Pedro id 1292.'
NOTE_CVSAC_B20X1T = 'Gc bulk 20 × 25g veg samosa (500g) × 1. No sleeve/box in Pedro tree. Pedro id 1484.'
NOTE_CVSAC_B20X4T = 'Booker bulk 20 × 25g veg samosa (500g) × 4. Sleeve S1470-05, box OC0016. Pedro id 1276.'
NOTE_CVSAL_B10T  = 'Gc bulk 10 × 100g G&G veg samosa (1000g) × 1. No box in Pedro tree. Pedro id 1453.'
NOTE_CVSAL_B20T  = 'Gc bulk 20 × 100g G&G veg samosa (2000g) × 1. No box in Pedro tree. Pedro id 1467.'


class Command(BaseCommand):
    help = 'Create veg samosa FG variant drafts. Never activates.'

    def handle(self, *args, **options):
        results = []

        with transaction.atomic():
            # ── packed items ──────────────────────────────────────────────────
            cvsac, c = make_product('CVSAC', 'SK4 - 4 x Vegetable Samosa - 003R - 40g | 160g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'],
                                    'GFF238MC', True, NOTE_CVSAC)
            results.append(f"{'NEW' if c else 'reuse'} CVSAC id={cvsac.id}")

            cvsal10, c = make_product('CVSAL10', 'LARGE - 10 x Vegetable Samosa - 003R - 100g | 1000g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'],
                                      None, True, NOTE_CVSAL10)
            results.append(f"{'NEW' if c else 'reuse'} CVSAL10 id={cvsal10.id}")

            cvsal20, c = make_product('CVSAL20', 'LARGE - 20 x Vegetable Samosa - 003R - 100g | 2000g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'],
                                      None, True, NOTE_CVSAL20)
            results.append(f"{'NEW' if c else 'reuse'} CVSAL20 id={cvsal20.id}")

            cvsac20, c = make_product('CVSAC20', 'LARGE - 20 x Vegetable Samosa - 003R - 25g | 500g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'],
                                      None, True, NOTE_CVSAC20)
            results.append(f"{'NEW' if c else 'reuse'} CVSAC20 id={cvsac20.id}")

            # empty drafts for packed items (no recipe yet — user to complete)
            for p, note in [(cvsac, NOTE_CVSAC), (cvsal10, NOTE_CVSAL10),
                            (cvsal20, NOTE_CVSAL20), (cvsac20, NOTE_CVSAC20)]:
                make_draft(p, note)
                clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            # ── look up existing codes ─────────────────────────────────────────
            need = require_products(
                'CVSAL', 'OC003', 'OC008', 'OC002', 'OC0016', 'S775-07', 'S666-06', 'S1470-05',
            )

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                # (stock_code, pedro_name, pack_weight_g, items_per_unit, note, lines)
                # lines = [(child_code_or_product, qty, unit_id)]
                ('CVSAC-R6T',
                 'Gazebo - Vegetable Samosa x 4 | 160G X 6', 960, 6, NOTE_CVSAC_R6T,
                 [(cvsac, 6, U_UNIT), (need['S775-07'], 6, U_UNIT), (need['OC008'], 1, U_UNIT)]),

                ('CVSAC-R6TEB',
                 'Booths Snacks - Vegetable Samosa x 4 | 160G X 6', 960, 6, NOTE_CVSAC_R6TEB,
                 [(cvsac, 6, U_UNIT), (need['S666-06'], 6, U_UNIT), (need['OC002'], 1, U_UNIT)]),

                ('CVSAC-R6TTA',
                 'TA - Vegetable Samosa x 4 | 160G X 6', 960, 6, NOTE_CVSAC_R6TTA,
                 [(cvsac, 6, U_UNIT)]),

                ('CVSAC-B20X1T',
                 'Gc - Vegetable Samosa 25G x 20 | 500G X 1', 500, 1, NOTE_CVSAC_B20X1T,
                 [(cvsac20, 1, U_UNIT)]),

                ('CVSAC-B20X4T',
                 'BOOKER - Gc - Vegetable Samosa x 20 | 500G X 4', 2000, 4, NOTE_CVSAC_B20X4T,
                 [(cvsac20, 4, U_UNIT), (need['S1470-05'], 4, U_UNIT), (need['OC0016'], 1, U_UNIT)]),

                ('CVSAL-B10T',
                 'Gc - Vegetable Samosa x 10 | 1000G', 1000, 1, NOTE_CVSAL_B10T,
                 [(cvsal10, 1, U_UNIT)]),

                ('CVSAL-B20T',
                 'Gc - Vegetable Samosa x 20 | 2000G X 1', 2000, 1, NOTE_CVSAL_B20T,
                 [(cvsal20, 1, U_UNIT)]),
            ]

            CVSAL = need['CVSAL']
            for stock_code, name, pack_g, items, note, lines in fg_specs:
                fg, created = make_product(
                    stock_code, name, CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'],
                    None, True, note,
                )
                results.append(f"{'NEW' if created else 'reuse'} {stock_code} id={fg.id}")

                ProductPackaging.objects.update_or_create(
                    product_id=fg.id,
                    defaults={'items_per_unit': Decimal(str(items)),
                              'pack_weight': Decimal(str(pack_g))},
                )
                ProductShelfLife.objects.get_or_create(product_id=fg.id, defaults={'shelf_life_days': 14})
                flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
                if not flags.is_sales_item:
                    flags.is_sales_item = True; flags.has_plan = True
                    flags.save(update_fields=['is_sales_item', 'has_plan'])

                v = make_draft(fg, note)
                clear_lines(v)
                for i, (child, qty, unit_id) in enumerate(lines, start=1):
                    add_line(v, i, child, qty, unit_id)
                sync_has_recipe(fg.id)

        for r in results:
            self.stdout.write(r)
        self.stdout.write(self.style.SUCCESS('veg samosa batch done'))
