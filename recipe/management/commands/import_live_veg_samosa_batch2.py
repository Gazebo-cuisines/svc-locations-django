"""Veg samosa FG variants — batch 2 (frozen + SP + jumbo).

Covers: FVSAL-1G6T, FVSAL-B20B, FVSAC-B20X4T, FVSAC-B20X4TDA,
        FVSAC-B30B, FVSAC-B50B, FVSAC-R6T, FVSAJ-B15B,
        CVSAJ-B10T, CVSAJ-B10X2T, CVSA1C1-1R6TSP

New packed items created: FVSAL-BAG20, B30VS40G, FVSAJ15, CVSAJ10
Jumbo samosa (CVSAJ10 / FVSAJ15) uses GFF039R chain — recipe left empty,
  note asks user to build from GFF039R Large Vegetable Samosa PDF.

  python manage.py import_live_veg_samosa_batch2
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    U_UNIT, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products,
)

CAT_PACKED = 76
CAT_FG = 127


class Command(BaseCommand):
    help = 'Create frozen/jumbo/SP veg samosa FG variant drafts. Never activates.'

    def handle(self, *args, **options):
        results = []

        with transaction.atomic():
            # ── new packed items ──────────────────────────────────────────────
            # Frozen G&G bag 20×100g
            fvsal_bag20, c = make_product(
                'FVSAL-BAG20', 'BAG - 20 x Vegetable Samosa - 003R - 100g | 2000g',
                CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False,
                'Frozen bulk bag of 20 G&G veg samosas. '
                'Please add the bag/tray code and recipe lines once the catalogue code is confirmed.',
            )
            results.append(f"{'NEW' if c else 'reuse'} FVSAL-BAG20 id={fvsal_bag20.id}")

            # Brakes bag 30×40g
            b30vs40g, c = make_product(
                'B30VS40G', 'BAG - 30 x Vegetable Samosa - 003R - 40g | 1200g',
                CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False,
                'Frozen bulk bag of 30 × 40g veg samosas. '
                'Please add the belt/fry and bag lines once the 40g process chain codes are confirmed.',
            )
            results.append(f"{'NEW' if c else 'reuse'} B30VS40G id={b30vs40g.id}")

            # Jumbo 140g samosa tray (003R-based — actually 039R large samosa)
            cvsaj10, c = make_product(
                'CVSAJ10', 'LARGE - 10 x Vegetable Samosa - 039R - 140g | 1400g',
                CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True,
                'Jumbo 140g vegetable samosa tray (10 units, GFF039R Large Veg Samosa). '
                'Please build this recipe from GFF039R signed PDF (harvi/1 LR/Signed copy-Print from here/). '
                'GFF039R chain is separate from GFF003R (100g/40g samosa).',
            )
            results.append(f"{'NEW' if c else 'reuse'} CVSAJ10 id={cvsaj10.id}")

            fvsaj15, c = make_product(
                'FVSAJ15', 'BAG - 15 x Vegetable Samosa - 039R - 140g | 2100g',
                CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False,
                'Frozen bag of 15 × 140g jumbo veg samosas (GFF039R chain). '
                'Please build from GFF039R PDF and confirm bag code.',
            )
            results.append(f"{'NEW' if c else 'reuse'} FVSAJ15 id={fvsaj15.id}")

            for p in (fvsal_bag20, b30vs40g, cvsaj10, fvsaj15):
                make_draft(p, p.remarks)
                clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            # ── existing codes ────────────────────────────────────────────────
            need = require_products('CVSAC', 'CVSAC20', 'OC001', 'OC002', 'OC0010', 'OC0016', 'S775-01')

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('FVSAL-1G6T',
                 'GZFR - Vegetable Samosa x 1 | 100G X 6 | Frozen', 600, 6,
                 'Frozen Gazebo G&G veg samosa 6-pack. No Pedro tree — built from product name. '
                 'Please confirm sleeve and box codes.',
                 []),   # no tree data, leave empty

                ('FVSAL-B20B',
                 'Gc - Vegetable Samosa x 20 | 2000G X 1 BAG', 2000, 1,
                 'Frozen bulk bag 20 × 100g veg samosa. Box OC001. Pedro id 1477.',
                 [(fvsal_bag20, 1, U_UNIT), (need['OC001'], 1, U_UNIT)]),

                ('FVSAC-B20X4T',
                 'GC - Vegetable Samosa 25G x 20 | 500G X 4', 2000, 4,
                 'Frozen Costco 20 × 25g veg samosa × 4. Box OC0016. Pedro id 2483.',
                 [(need['CVSAC20'], 4, U_UNIT), (need['OC0016'], 1, U_UNIT)]),

                ('FVSAC-B20X4TDA',
                 'GCDA - Vegetable Samosa X 20 | 500G X 4', 2000, 4,
                 'Frozen DA 20 × 25g veg samosa × 4. No Pedro children. Pedro id 3405.',
                 [(need['CVSAC20'], 4, U_UNIT)]),

                ('FVSAC-B30B',
                 'BRAKES - Gc - Vegetable Samosa 40G x 30 | 1200G', 1200, 1,
                 'Frozen Brakes bag 30 × 40g veg samosa. Box OC0010. Pedro id 1505.',
                 [(b30vs40g, 1, U_UNIT), (need['OC0010'], 1, U_UNIT)]),

                ('FVSAC-B50B',
                 'Gc - Vegetable Samosa x 50 | 2000G X 1', 2000, 1,
                 'Frozen bulk 50 × 40g veg samosa. Box OC002. Pedro id 1476. '
                 'Packed item not confirmed in Pedro — please add if different.',
                 [(need['OC002'], 1, U_UNIT)]),

                ('FVSAC-R6T',
                 'GC - Vegetable Samosa x 4 | 160G X 6 | FRANCE', 960, 6,
                 'Frozen GC France 4-pack veg samosa × 6. Box OC002. Pedro id 2498.',
                 [(need['CVSAC'], 6, U_UNIT), (need['OC002'], 1, U_UNIT)]),

                ('FVSAJ-B15B',
                 'Gc - Vegetable Samosa x 15 | Jumbo | 2100G X 1', 2100, 1,
                 'Frozen bulk 15 × 140g jumbo veg samosa (GFF039R). Box OC002. Pedro id 1480. '
                 'Jumbo chain needs GFF039R recipe — please complete FVSAJ15 recipe first.',
                 [(fvsaj15, 1, U_UNIT), (need['OC002'], 1, U_UNIT)]),

                ('CVSAJ-B10T',
                 'Gc - Vegetable Samosa x 10 | Jumbo | 1400G X 1', 1400, 1,
                 'Jumbo 140g veg samosa × 10 (GFF039R). No box in Pedro tree. Pedro id 1466. '
                 'Please complete CVSAJ10 recipe from GFF039R PDF first.',
                 [(cvsaj10, 1, U_UNIT)]),

                ('CVSAJ-B10X2T',
                 'Gc - Vegetable Samosa 140G x 10 | 1400G x 2', 2800, 2,
                 'Jumbo 140g veg samosa 10-tray × 2. Box OC0016. Pedro id 2313.',
                 [(cvsaj10, 2, U_UNIT), (need['OC0016'], 1, U_UNIT)]),

                ('CVSA1C1-1R6TSP',
                 'SP - Vegetable Samosa x 4 | 160G X 6', 960, 6,
                 'SP 4-pack veg samosa × 6. Sleeve S775-01, box OC002. Pedro id 1283.',
                 [(need['CVSAC'], 6, U_UNIT), (need['S775-01'], 6, U_UNIT), (need['OC002'], 1, U_UNIT)]),
            ]

            for stock_code, name, pack_g, items, note, lines in fg_specs:
                fg, created = make_product(
                    stock_code, name, CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'],
                    None, False, note,
                )
                results.append(f"{'NEW' if created else 'reuse'} {stock_code} id={fg.id}")

                ProductPackaging.objects.update_or_create(
                    product_id=fg.id,
                    defaults={'items_per_unit': Decimal(str(items)),
                              'pack_weight': Decimal(str(pack_g))},
                )
                ProductShelfLife.objects.get_or_create(product_id=fg.id, defaults={'shelf_life_days': 365})
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
        self.stdout.write(self.style.SUCCESS('veg samosa batch 2 done'))
