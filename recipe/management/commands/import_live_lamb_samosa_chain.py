"""Lamb samosa full chain + all FG variants.

GFF005R Lamb Samosa V.22 — PDF total 211160g.
Water (400g) omitted — no catalogue code. Logged in anomaly.

Chain: GFF005R-S (existing 706) → GFF005R-Mx → GFF005R-B-100 → GFF005R-F-100
       → CLSAL (packed) → FG variants

  python manage.py import_live_lamb_samosa_chain
"""
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, STAMP, U_UNIT, U_G, U_TRAY, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MX = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF005R LAMB SAMOSA V.22.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_MX = (
    'Source: GFF005R V.22 (signed). PDF total 211160g. '
    'Water 400g omitted — no catalogue code (please add Water/Mains Water). '
    'Imported sum: 210760g.'
)
NOTE_BELT = 'Pedro tblproducttree source. Mix grams ~80.2g per unit estimated from batch ratio.'
NOTE_FRY = 'Pedro tblproducttree source — passthrough from belt.'
NOTE_CLSAL = (
    'G&G 1 × 100g lamb samosa packed item. GFF240MC QAS. '
    'Please confirm film code and tray code (Pedro: PKLEE001-13 tray).'
)
NOTE_CLSAL_FG = (
    'G&G lamb samosa 6-pack. Box OC0014. Sleeve PKTHE006-02 (G&G Lamb Samosa). Pedro id 1229.'
)


class Command(BaseCommand):
    help = 'Create lamb samosa chain + FG drafts from GFF005R PDF. Never activates.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'GFF005R-S', 'VEGCHI-05', 'VEGFRO-01', 'PROTEIN-04',
                'INGRAD-01', 'INGRAD-06', 'PASTRY-01',
                'PKLEE001-13', 'PKTHE006-02', 'OC0014', 'OC002', 'OC0016', 'OC001', 'OC0010',
                'S1470-03', 'S666-03', 'S775TK-03', 'OC0012',
            )

            # ── mixer ─────────────────────────────────────────────────────────
            mx, c = make_product('GFF005R-Mx', 'Lamb Samosa - 005 - Mixer',
                                 CAT_SPICE, U_G, LOC['lwr'], LOC['belt'],
                                 'GFF005R-Mx', False, NOTE_MX)
            results.append(f"{'NEW' if c else 'reuse'} GFF005R-Mx id={mx.id}")

            belt, c = make_product('GFF005R-B-100', 'Lamb Samosa - 100 grams - 005 - Belt',
                                   CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'],
                                   None, False, NOTE_BELT)
            results.append(f"{'NEW' if c else 'reuse'} GFF005R-B-100 id={belt.id}")

            fry, c = make_product('GFF005R - F - 100', 'Lamb Samosa - 100 grams - 005 - Frying',
                                  CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'],
                                  None, False, NOTE_FRY)
            results.append(f"{'NEW' if c else 'reuse'} GFF005R - F - 100 id={fry.id}")

            clsal, c = make_product('CLSAL', 'GG - 1 x Lamb Samosa - 005R - 100g - SQ TRAY | 100g',
                                    CAT_PACKED, U_UNIT, LOC['hr'], LOC['sleeve'],
                                    'GFF240MC', True, NOTE_CLSAL)
            results.append(f"{'NEW' if c else 'reuse'} CLSAL id={clsal.id}")

            # ── new bulk/institutional packed items ───────────────────────────
            packed_items = [
                ('CLSAC', 'SK4 - 4 x Lamb Samosa - 005R - 40g | 160g', 'GFF005R 40g chain — please add belt/fry lines once 40g process codes confirmed.'),
                ('CLSAC18', 'LARGE - 18 x Lamb Samosa - 005R - 25g | 450g', 'GFF005R 25g chain — please add belt/fry lines once 25g process codes confirmed.'),
                ('CLSAL20', 'LARGE - 20 x Lamb Samosa - 005R - 75g | 1500g', 'GFF005R 75g chain — please add belt/fry lines once 75g process codes confirmed.'),
                ('CLSAJ10', 'LARGE - 10 x Lamb Samosa - 005R - 120g | 1200g', 'GFF005R 120g jumbo chain — please add belt/fry lines once 120g process codes confirmed.'),
                ('FLSAC-BAG30', 'BAG - 30 x Lamb Samosa - 005R - 40g | 1200g', 'Frozen bag 30 × 40g lamb samosa. GFF005R chain. Please add bag code.'),
                ('FLSAC1', '2171F - 4 x Lamb Samosa - 510R - 40g | 160g', 'Cook-format lamb samosa 4-pack (GFF509R/510R chain). Please add recipe from GFF509R/510R PDFs.'),
                ('FLSAM-BAG20', 'BAG - 20 x Lamb Samosa - 005R - 75g | 1500g', 'Frozen bag 20 × 75g lamb samosa. GFF005R chain. Please add bag code.'),
            ]
            packed = {}
            for code, name, note in packed_items:
                p, c = make_product(code, name, CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False, note)
                packed[code] = p
                results.append(f"{'NEW' if c else 'reuse'} {code} id={p.id}")
                make_draft(p, note)
                clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            # ── mixer recipe ──────────────────────────────────────────────────
            spice = needed['GFF005R-S']
            v_mx = make_draft(mx, NOTE_MX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['VEGCHI-05'], 72000, U_G)
            add_line(v_mx, 2, needed['VEGFRO-01'], 72000, U_G)
            add_line(v_mx, 3, needed['PROTEIN-04'], 48000, U_G)
            add_line(v_mx, 4, needed['INGRAD-01'], 4000, U_G)
            add_line(v_mx, 5, needed['INGRAD-06'], 6400, U_G)
            # Water 400g omitted — no catalogue code
            add_line(v_mx, 6, spice, 8360, U_G)
            v_mx.batch_quantity = Decimal('210760')
            v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_mx, PDF_MX, 'GFF005R V.22 mixer evidence')

            # ── belt / fry ────────────────────────────────────────────────────
            v_b = make_draft(belt, NOTE_BELT)
            clear_lines(v_b)
            add_line(v_b, 1, mx, '80.2', U_G)
            add_line(v_b, 2, needed['PASTRY-01'], 1, U_UNIT)

            v_f = make_draft(fry, NOTE_FRY)
            clear_lines(v_f)
            add_line(v_f, 1, belt, 1, U_UNIT)

            # ── CLSAL packed item ─────────────────────────────────────────────
            v_hr = make_draft(clsal, NOTE_CLSAL)
            clear_lines(v_hr)
            add_line(v_hr, 1, fry, 1, U_UNIT)
            add_line(v_hr, 2, needed['PKLEE001-13'], 1, U_TRAY)
            ProductPackaging.objects.update_or_create(
                product_id=clsal.id, defaults={'pack_weight': Decimal('100')},
            )

            for p in (mx, belt, fry, clsal):
                sync_has_recipe(p.id)

            # ── FG variants ───────────────────────────────────────────────────
            fg_specs = [
                ('CLSAL-1G6T', 'Gazebo - G&G - Lamb Samosa | 100G X 6', 600, 6, True,
                 NOTE_CLSAL_FG,
                 [(clsal, 6, U_UNIT), (needed['PKTHE006-02'], 6, U_UNIT), (needed['OC0014'], 1, U_UNIT)]),

                ('CLSAL-B20T', 'Gc - Lamb Samosa x 20 | 1500G X 1', 1500, 1, True,
                 'Gc bulk 20 × 75g lamb samosa. Pedro id 1463. Please confirm exact weight/tray.',
                 [(packed['CLSAL20'], 1, U_UNIT)]),

                ('CLSAC-B18X4T', 'BOOKER - Gc - Lamb Samosas x 18 | 450G X 4', 1800, 4, True,
                 'Booker 18 × 25g lamb samosa ×4. Sleeve S1470-03, box OC0016. Pedro id 1274.',
                 [(packed['CLSAC18'], 4, U_UNIT), (needed['S1470-03'], 4, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('CLSAC-R6TEB', 'Booths Snacks - Lamb Samosa | 160G X 6', 960, 6, True,
                 'Booths 4-pack lamb samosa ×6. Sleeve S666-03, box OC002. Pedro id 1244.',
                 [(packed['CLSAC'], 6, U_UNIT), (needed['S666-03'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('CLSAC-R6TTA', 'TA - Lamb Samosa x 4 | 160G X 6', 960, 6, True,
                 'TA 4-pack lamb samosa ×6. Sleeve S775TK-03, box OC0013. Pedro id 1291.',
                 [(packed['CLSAC'], 6, U_UNIT), (needed['S775TK-03'], 6, U_UNIT)]),

                ('CLSAJ-B10T', 'Gc - Lamb Samosa x 10 | Jumbo | 1200G X 1', 1200, 1, True,
                 'Gc jumbo 120g lamb samosa ×10. No box in Pedro. Pedro id 1462.',
                 [(packed['CLSAJ10'], 1, U_UNIT)]),

                ('FLSAC-B18X4TDA', 'GCDA - Lamb Samosa x 18 | 450G X 4', 1800, 4, False,
                 'Frozen DA 18 × 25g lamb samosa ×4. No Pedro children (id 3356).',
                 [(packed['CLSAC18'], 4, U_UNIT)]),

                ('FLSAC-B30B', 'BRAKES - Gc - Lamb Samosa 40G x 30 | 1200G', 1200, 1, False,
                 'Frozen Brakes 30 × 40g lamb samosa. Box OC0010. Pedro id 1506.',
                 [(packed['FLSAC-BAG30'], 1, U_UNIT), (needed['OC0010'], 1, U_UNIT)]),

                ('FLSAC-B50B', 'Gc - Lamb Samosa x 50 | 2000G X 1', 2000, 1, False,
                 'Frozen bulk 50 lamb samosa. Box OC002. Pedro id 1472. Packed item code TBC.',
                 [(needed['OC002'], 1, U_UNIT)]),

                ('FLSAC2-R8TCO', 'COOK - Lamb Samosa x 4 | 160G x 8', 1280, 8, False,
                 'Cook 4-pack lamb samosa ×8 (GFF509R/510R chain). Box OC0012. Pedro id 2508. '
                 'Please complete FLSAC1 recipe from GFF509R/510R PDFs first.',
                 [(packed['FLSAC1'], 8, U_UNIT), (needed['OC0012'], 1, U_UNIT)]),

                ('FLSAL-1G6T', 'GZFR - Lamb Samosa x 1 | 100G X 6 | Frozen', 600, 6, False,
                 'Frozen Gazebo G&G lamb samosa 6-pack. No Pedro tree (id 3316). '
                 'Please confirm sleeve and box codes.',
                 [(clsal, 6, U_UNIT)]),

                ('FLSAM-B20B', 'Gc - Lamb Samosa x 20 | 1500G X 1 BAG', 1500, 1, False,
                 'Frozen Gc 20 × 75g lamb samosa bag. Box OC001. Pedro id 1471.',
                 [(packed['FLSAM-BAG20'], 1, U_UNIT), (needed['OC001'], 1, U_UNIT)]),
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
                    product_id=fg.id, defaults={'shelf_life_days': 14 if chilled else 365},
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
        self.stdout.write(self.style.SUCCESS('lamb samosa chain done'))
