"""Chicken samosa FG variants — batch import.

Uses existing CCSAL packed item (829). Creates new packed items as needed.
Jumbo uses GFF038R chain (separate from GFF004R).

  python manage.py import_live_chicken_samosa_batch
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    U_UNIT, LOC,
    make_product, make_draft, add_line, clear_lines, require_products,
)

CAT_PACKED = 76
CAT_FG = 127


class Command(BaseCommand):
    help = 'Create chicken samosa FG variant drafts.'

    def handle(self, *args, **options):
        results = []

        with transaction.atomic():
            # ── new packed items ──────────────────────────────────────────────
            NOTE_CCSAC = (
                'SK4 tray of 4 chicken tikka samosas (40g each, 160g total). GFF004R chain. '
                'Please add belt/fry lines once GFF004R-F-40 code is confirmed.'
            )
            NOTE_CCSAC20 = (
                '20 × 25g chicken tikka samosas bulk tray (500g). GFF004R chain. '
                'Please add belt/fry lines once 25g process chain codes are confirmed.'
            )
            NOTE_CCSAM10 = (
                '10 × 75g chicken tikka samosas medium tray (750g). GFF004R chain. '
                'Please add lines once 75g process chain codes are confirmed.'
            )
            NOTE_CCSAM20 = (
                '20 × 75g chicken tikka samosas medium tray (1500g). GFF004R chain. '
                'Please add lines once 75g process chain codes are confirmed.'
            )
            NOTE_CCSAJ10 = (
                'Jumbo 120g chicken samosa × 10 tray (1200g). GFF038R chain (Extra Large Chicken Samosa). '
                'Please build from GFF038R signed PDF in harvi/1 LR/Signed copy-Print from here/.'
            )
            NOTE_FCSAJ_BAG = (
                'Frozen bag of 15 × 120g jumbo chicken samosas (1800g). GFF038R chain. '
                'Please complete CCSAJ10 recipe from GFF038R PDF first.'
            )
            NOTE_FCSAM_BAG = (
                'Frozen bag of 20 × 75g chicken tikka samosas (1500g). GFF004R chain. '
                'Please add lines once 75g process chain codes are confirmed.'
            )

            ccsac, c = make_product('CCSAC', 'SK4 - 4 x Chicken Tikka Samosa - 004R - 40g | 160g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF201MC', True, NOTE_CCSAC)
            results.append(f"{'NEW' if c else 'reuse'} CCSAC id={ccsac.id}")

            ccsac20, c = make_product('CCSAC20', 'LARGE - 20 x Chicken Tikka Samosa - 004R - 25g | 500g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True, NOTE_CCSAC20)
            results.append(f"{'NEW' if c else 'reuse'} CCSAC20 id={ccsac20.id}")

            ccsam10, c = make_product('CCSAM10', 'LARGE - 10 x Chicken Tikka Samosa - 004R - 75g | 750g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True, NOTE_CCSAM10)
            results.append(f"{'NEW' if c else 'reuse'} CCSAM10 id={ccsam10.id}")

            ccsam20, c = make_product('CCSAM20', 'LARGE - 20 x Chicken Tikka Samosa - 004R - 75g | 1500g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True, NOTE_CCSAM20)
            results.append(f"{'NEW' if c else 'reuse'} CCSAM20 id={ccsam20.id}")

            ccsaj10, c = make_product('CCSAJ10', 'LARGE - 10 x Chicken Samosa - 038R - 120g | 1200g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True, NOTE_CCSAJ10)
            results.append(f"{'NEW' if c else 'reuse'} CCSAJ10 id={ccsaj10.id}")

            fcsaj_bag15, c = make_product('FCSAJ-BAG15', 'BAG - 15 x Chicken Samosa - 038R - 120g | 1800g',
                                          CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False, NOTE_FCSAJ_BAG)
            results.append(f"{'NEW' if c else 'reuse'} FCSAJ-BAG15 id={fcsaj_bag15.id}")

            fcsam_bag20, c = make_product('FCSAM-BAG20', 'BAG - 20 x Chicken Tikka Samosa - 004R - 75g | 1500g',
                                          CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False, NOTE_FCSAM_BAG)
            results.append(f"{'NEW' if c else 'reuse'} FCSAM-BAG20 id={fcsam_bag20.id}")

            for p in (ccsac, ccsac20, ccsam10, ccsam20, ccsaj10, fcsaj_bag15, fcsam_bag20):
                make_draft(p, p.remarks)
                clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            need = require_products(
                'CCSAL', 'OC003', 'OC002', 'OC008', 'OC0016', 'OC001',
                'S775-06', 'S775TK-02', 'S1470-02',
            )

            fg_specs = [
                ('CCSAL-G12T',
                 'Gazebo - G&G - Chicken Tikka Samosa | 100G X 12', 1200, 12, True,
                 'Gazebo G&G chicken tikka samosa 12-pack. Box OC003. Pedro id 1239.',
                 [(need['CCSAL'], 12, U_UNIT), (need['OC003'], 1, U_UNIT)]),

                ('CCSAC-R6T',
                 'Gazebo - Chicken Tikka Samosa x 4 | 160G X 6', 960, 6, True,
                 'Gazebo 4-pack CK samosa ×6. Sleeve S775-06, box OC008. Pedro id 1278.',
                 [(ccsac, 6, U_UNIT), (need['S775-06'], 6, U_UNIT), (need['OC008'], 1, U_UNIT)]),

                ('CCSAC-R6TTA',
                 'TA - Chicken Tikka Samosa x 4 | 160G X 6', 960, 6, True,
                 'TA 4-pack CK samosa ×6. Sleeve S775TK-02, box OC0013. Pedro id 1289.',
                 [(ccsac, 6, U_UNIT), (need['S775TK-02'], 6, U_UNIT)]),

                ('CCSAC-B20X1T',
                 'Gc - Chicken Tikka Samosa x 20 | 500G X 1', 500, 1, True,
                 'Gc bulk 20 × 25g CK samosa. No box in Pedro tree. Pedro id 1483.',
                 [(ccsac20, 1, U_UNIT)]),

                ('CCSAC-B20X4T',
                 'BOOKER - Gc - Chicken Tikka Samosa x 20 | 500G X 4', 2000, 4, True,
                 'Booker bulk 20 × 25g CK samosa ×4. Sleeve S1470-02, box OC0016. Pedro id 1273.',
                 [(ccsac20, 4, U_UNIT), (need['S1470-02'], 4, U_UNIT), (need['OC0016'], 1, U_UNIT)]),

                ('CCSAC2-B25X4TTA',
                 'TA - Chicken Samosa 35G with Tamarind Dip x 25 | 995G x 4', 3980, 4, True,
                 'TA 25 × 35g CK samosa + tamarind dip × 4. No Pedro tree (id 2563). '
                 'Tamarind dip missing from catalogue — please add code. '
                 'Packed item assumed CCSAC20-equivalent (25g portion).',
                 [(ccsac20, 4, U_UNIT)]),

                ('CCSAC3-B24X8T',
                 'COSTCO - GC - 24 Chicken Samosa 40G | 960G X 8', 7680, 8, True,
                 'Costco 24 × 40g CK samosa ×8. No Pedro tree (id 3432). Packed item assumed CCSAC.',
                 [(ccsac, 8, U_UNIT)]),

                ('CCSAJ-B10T',
                 'Gc - Chicken Samosa x 10 | Jumbo | 1200G X 1', 1200, 1, True,
                 'Gc jumbo 120g CK samosa ×10. No box in Pedro tree. Pedro id 1460. '
                 'Needs GFF038R chain — please complete CCSAJ10 recipe first.',
                 [(ccsaj10, 1, U_UNIT)]),

                ('CCSAM-B10T',
                 'Gc - Chicken Tikka Samosa x 10 | 750G X 1', 750, 1, True,
                 'Gc medium 75g CK samosa ×10. No box in Pedro tree. Pedro id 1448.',
                 [(ccsam10, 1, U_UNIT)]),

                ('CCSAM-B20T',
                 'Gc - Chicken Tikka Samosa x 20 | 1500G X 1 TRAY', 1500, 1, True,
                 'Gc medium 75g CK samosa ×20 tray. No box in Pedro tree. Pedro id 1461.',
                 [(ccsam20, 1, U_UNIT)]),

                ('FCSAC-R6T',
                 'GZFR - Chicken Tikka Samosa x 4 | 160G x 6', 960, 6, False,
                 'Frozen Gazebo 4-pack CK samosa ×6. Box OC002. Pedro id 2272.',
                 [(ccsac, 6, U_UNIT), (need['OC002'], 1, U_UNIT)]),

                ('FCSAC-B20X4TDA',
                 'GCDA - Chicken Tikka Samosa X 20 | 500G X 4', 2000, 4, False,
                 'Frozen DA 20 × 25g CK samosa ×4. No Pedro children (id 3355).',
                 [(ccsac20, 4, U_UNIT)]),

                ('FCSAJ-B15B',
                 'Gc - Chicken Tikka Samosa x 15 | Jumbo | 1800G X 1', 1800, 1, False,
                 'Frozen Gc jumbo 120g CK samosa ×15. Box OC002. Pedro id 1481. '
                 'Needs GFF038R — please complete CCSAJ10 first.',
                 [(fcsaj_bag15, 1, U_UNIT), (need['OC002'], 1, U_UNIT)]),

                ('FCSAJ1-B15B',
                 'DA - Chicken Tikka Samosa x 15 | Jumbo | 1800G X 1', 1800, 1, False,
                 'DA jumbo 120g CK samosa ×15. No Pedro tree (id 3314). Needs GFF038R.',
                 [(fcsaj_bag15, 1, U_UNIT)]),

                ('FCSAM-B20B',
                 'Gc - Chicken Tikka Samosa x 20 | 1500G X 1 BAG', 1500, 1, False,
                 'Frozen Gc medium 75g CK samosa ×20 bag. Box OC001. Pedro id 1469.',
                 [(fcsam_bag20, 1, U_UNIT), (need['OC001'], 1, U_UNIT)]),
            ]

            for stock_code, name, pack_g, items, chilled, note, lines in fg_specs:
                fg, created = make_product(
                    stock_code, name, CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'],
                    None, chilled, note,
                )
                results.append(f"{'NEW' if created else 'reuse'} {stock_code} id={fg.id}")

                ProductPackaging.objects.update_or_create(
                    product_id=fg.id,
                    defaults={'items_per_unit': Decimal(str(items)),
                              'pack_weight': Decimal(str(pack_g))},
                )
                ProductShelfLife.objects.get_or_create(
                    product_id=fg.id,
                    defaults={'shelf_life_days': 14 if chilled else 365},
                )
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
        self.stdout.write(self.style.SUCCESS('chicken samosa batch done'))
