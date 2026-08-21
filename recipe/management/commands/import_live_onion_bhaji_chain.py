"""Onion Bhaji GFF121R chain + all FG variants.

GFF121R V.5 — PDF total 128700g ✓ (100000+22000+2000+4700).
GFF121R-S spice — PDF total 4700g ✓.

FOBH1C-B4KGB uses GFF009-4R (low salt apetito) — different chain.
  Recipe left empty — please import from GFF009-4R PDF separately.

  python manage.py import_live_onion_bhaji_chain
"""
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MX = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF121R ONION BHAJI 40g -V.5.pdf'
PDF_SP = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF121R-S-Onion Bhaji 40g (GFF121R-S) - Spices V.4.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_SP = 'Source: GFF121R-S V.4. PDF total 4700g ✓.'
NOTE_MX = 'Source: GFF121R V.5. PDF total 128700g ✓. Sliced Onion 100000 + Gram Flour 22000 + Lemon Juice 2000 + Spice 4700.'
NOTE_BELT = 'Pedro source — portion size per belt (size-specific). Pastry not applicable for bhaji.'
NOTE_FRY = 'Pedro source — passthrough from portioning/frying stage.'


class Command(BaseCommand):
    help = 'Create onion bhaji chain + FG drafts. Never activates.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'VEGCHI-01', 'INGRAD-02', 'SAUCE0-02',
                'SPICE0-02', 'SPICE0-04', 'SPICE0-05', 'SPICE0-06', 'SPICE0-08',
                'SPICE0-09', 'SPICE0-11', 'SPICE0-13', 'SPICE0-16', 'SPICE0-19',
                'S666-04', 'S1470-04', 'OC007', 'OC002', 'OC0016', 'OC001', 'OC0010',
            )

            # ── spice ─────────────────────────────────────────────────────────
            sp, c = make_product('GFF121R-S', 'Onion Bhaji 40g - Spices',
                                 CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF121R-S', False, NOTE_SP)
            results.append(f"{'NEW' if c else 'reuse'} GFF121R-S id={sp.id}")
            v_sp = make_draft(sp, NOTE_SP)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SPICE0-02'], 1000, U_G)
            add_line(v_sp, 2, needed['SPICE0-05'], 800, U_G)
            add_line(v_sp, 3, needed['SPICE0-09'], 600, U_G)
            add_line(v_sp, 4, needed['SPICE0-04'], 600, U_G)
            add_line(v_sp, 5, needed['SPICE0-13'], 400, U_G)
            add_line(v_sp, 6, needed['SPICE0-06'], 400, U_G)
            add_line(v_sp, 7, needed['SPICE0-08'], 300, U_G)
            add_line(v_sp, 8, needed['SPICE0-11'], 200, U_G)
            add_line(v_sp, 9, needed['SPICE0-19'], 200, U_G)
            add_line(v_sp, 10, needed['SPICE0-16'], 200, U_G)
            v_sp.batch_quantity = Decimal('4700'); v_sp.batch_unit_id = U_G
            v_sp.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_sp, PDF_SP, 'GFF121R-S V.4 spice evidence')

            # ── mixer ─────────────────────────────────────────────────────────
            mx, c = make_product('GFF121R-Mx', 'Onion Bhaji - 121 - Mixer',
                                 CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF121R-Mx', False, NOTE_MX)
            results.append(f"{'NEW' if c else 'reuse'} GFF121R-Mx id={mx.id}")
            v_mx = make_draft(mx, NOTE_MX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['VEGCHI-01'], 100000, U_G)
            add_line(v_mx, 2, needed['INGRAD-02'], 22000, U_G)
            add_line(v_mx, 3, needed['SAUCE0-02'], 2000, U_G)
            add_line(v_mx, 4, sp, 4700, U_G)
            v_mx.batch_quantity = Decimal('128700'); v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_mx, PDF_MX, 'GFF121R V.5 mixer evidence')

            # ── belt / fry by size ────────────────────────────────────────────
            process_products = {}
            for size_g, mix_g in [(30, 29), (40, 39), (100, 97)]:
                b, c = make_product(f'GFF121R-B-{size_g}', f'Onion Bhaji - {size_g}g - 121 - Portioning',
                                    CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_BELT)
                results.append(f"{'NEW' if c else 'reuse'} GFF121R-B-{size_g} id={b.id}")
                v_b = make_draft(b, NOTE_BELT)
                clear_lines(v_b)
                add_line(v_b, 1, mx, str(mix_g), U_G)

                f, c = make_product(f'GFF121R - F - {size_g}', f'Onion Bhaji - {size_g}g - 121 - Frying',
                                    CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_FRY)
                results.append(f"{'NEW' if c else 'reuse'} GFF121R - F - {size_g} id={f.id}")
                v_f = make_draft(f, NOTE_FRY)
                clear_lines(v_f)
                add_line(v_f, 1, b, 1, U_UNIT)
                process_products[size_g] = (b, f)
                sync_has_recipe(b.id); sync_has_recipe(f.id)

            fry40 = process_products[40][1]
            fry30 = process_products[30][1]
            fry100 = process_products[100][1]

            # ── packed items ──────────────────────────────────────────────────
            pnotes = {
                'COBHC3': 'GB duo pot 2×40g (80g) onion bhaji. GFF121R chain. Please add tray code once confirmed.',
                'COBHC-SK4': 'SK4 split tray 4×40g (160g) onion bhaji. GFF121R chain. Please add tray code.',
                'COBHC22': '22×30g onion bhaji bulk tray (660g). GFF121R chain. Please add tray code.',
                'COBH3C': '20×40g onion bhaji bulk tray (800g). GFF121R chain. Please add tray code.',
                'COBHL10': '10×100g onion bhaji bulk tray (1000g). GFF121R chain. Please add tray code.',
                'COBHL20': '20×100g onion bhaji bulk tray (2000g). GFF121R chain. Please add tray code.',
                'FOBHC3-BAG40': 'Frozen bag 40×40g onion bhaji (1600g). GFF121R chain. Please add bag code.',
                'FOBHL-BAG20': 'Frozen bag 20×100g onion bhaji (2000g). GFF121R chain. Please add bag code.',
                'COBHC3-1KG': '1kg tray onion bhaji (40g units × 25). GFF121R chain. Please add tray code.',
                'FOBHC3-SK4': 'Frozen SK4 4×40g onion bhaji (160g). GFF121R chain. Please add tray code.',
            }
            packed = {}
            fry_map = {
                'COBHC3': fry40, 'COBHC-SK4': fry40, 'COBHC22': fry30,
                'COBH3C': fry40, 'COBHL10': fry100, 'COBHL20': fry100,
                'FOBHC3-BAG40': fry40, 'FOBHL-BAG20': fry100,
                'COBHC3-1KG': fry40, 'FOBHC3-SK4': fry40,
            }
            for code, note in pnotes.items():
                p, c = make_product(code, f'{code} — Onion Bhaji Packed Item',
                                    CAT_PACKED, U_UNIT, LOC['fry'] if 'BAG' not in code else LOC['dispatch'],
                                    LOC['hr'] if 'BAG' not in code else LOC['dispatch'],
                                    None, False, note)
                packed[code] = p
                results.append(f"{'NEW' if c else 'reuse'} {code} id={p.id}")
                make_draft(p, note)
                clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            sync_has_recipe(sp.id); sync_has_recipe(mx.id)

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('COBHC3-G6T', 'Gazebo - GB - Onion Bhaji x 2 | 80G X 6', 480, 6, True,
                 'GB duo pot 2×40g bhaji ×6. Box OC007. Pedro id 1228.',
                 [(packed['COBHC3'], 6, U_UNIT), (needed['OC007'], 1, U_UNIT)]),

                ('COBHC3-G18T', 'Gazebo - Onion Bhaji x 2 | 80G X 18', 1440, 18, True,
                 'GB duo pot 2×40g bhaji ×18. Box OC001. Pedro id 2189.',
                 [(packed['COBHC3'], 18, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('COBHC3-R6T', 'Gazebo - Onion Bhaji x 4 | 160G X 6', 960, 6, True,
                 'Gazebo SK4 4-pack bhaji ×6. No Pedro children. Pedro id 1294.',
                 [(packed['COBHC-SK4'], 6, U_UNIT)]),

                ('COBHC3-R6TTA', 'TA - Onion Bhajis x 4 | 160G x 6', 960, 6, True,
                 'TA SK4 4-pack bhaji ×6. No Pedro children. Pedro id 2361.',
                 [(packed['COBHC-SK4'], 6, U_UNIT)]),

                ('COBHC-B22X1T', 'Gc - Onion Bhaji x 22 | 660G X 1', 660, 1, True,
                 'Gc 22×30g bhaji bulk tray. No box. Pedro id 1482.',
                 [(packed['COBHC22'], 1, U_UNIT)]),

                ('COBHC-B22X4T', 'BOOKER - Gc - Onion Bhaji x 22 | 660G X 4', 2640, 4, True,
                 'Booker 22×30g bhaji ×4. Sleeve S1470-04, box OC0016. Pedro id 1275.',
                 [(packed['COBHC22'], 4, U_UNIT), (needed['S1470-04'], 4, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('COBHC3-B1KGX2T', 'Gc - Onion Bhaji 40G | 1000G X 2', 2000, 2, True,
                 'Gc 1kg bhaji tray ×2. Box OC001. Pedro id 2314.',
                 [(packed['COBHC3-1KG'], 2, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('COBHL-B10T', 'Gc - Onion Bhaji x 10 | 1000G X 1', 1000, 1, True,
                 'Gc 10×100g bhaji bulk tray. No box. Pedro id 1450.',
                 [(packed['COBHL10'], 1, U_UNIT)]),

                ('COBHL-B20T', 'Gc - Onion Bhaji x 20 | 2000G X 1', 2000, 1, True,
                 'Gc 20×100g bhaji bulk tray. No box. Pedro id 1464.',
                 [(packed['COBHL20'], 1, U_UNIT)]),

                ('COBH3C-1R6TSP', 'SP - Onion Bhaji x 4 | 160G X 6', 960, 6, True,
                 'SP SK4 4-pack bhaji ×6. No Pedro children. Pedro id 1282.',
                 [(packed['COBHC-SK4'], 6, U_UNIT)]),

                ('COBH3C-B20X1TEB', 'Booths Deli - Onion Bhaji 40G x 20 | 800G x 1', 800, 1, True,
                 'Booths Deli 20×40g bhaji tray. No box. Pedro id 1490.',
                 [(packed['COBH3C'], 1, U_UNIT)]),

                ('COBH3C-R6TEB', 'Booths Snacks - Onion Bhaji | 160G X 6', 960, 6, True,
                 'Booths SK4 4-pack bhaji ×6. Sleeve S666-04, box OC002. Pedro id 1245.',
                 [(packed['COBHC-SK4'], 6, U_UNIT), (needed['S666-04'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('FOBHC-B22X4T', 'GC - Onion Bhaji 30G x 22 | 660G X 4', 2640, 4, False,
                 'Frozen Costco 22×30g bhaji ×4. Box OC0016. Pedro id 2482.',
                 [(packed['COBHC22'], 4, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('FOBHC-B22X4TDA', 'GCDA - Onion Bhaji x 22 | 660G X 4', 2640, 4, False,
                 'Frozen DA 22×30g bhaji ×4. No Pedro children (id 3357).',
                 [(packed['COBHC22'], 4, U_UNIT)]),

                ('FOBHC-B50B', 'Gc - Onion Bhaji x 50 | 1500G X 1', 1500, 1, False,
                 'Frozen 50×30g bhaji. Box OC002. Pedro id 1473. Packed item TBC.',
                 [(needed['OC002'], 1, U_UNIT)]),

                ('FOBHC-B50BVE', 'VE - Onion Bhaji 30G x 50 | 1500G X 1', 1500, 1, False,
                 'Frozen VE 50×30g bhaji. Box OC002. Pedro id 1503. Packed item TBC.',
                 [(needed['OC002'], 1, U_UNIT)]),

                ('FOBHC3-3R8TCO', 'COOK - Onion Bhaji x 4 | 160G X 8', 1280, 8, False,
                 'Cook 4-pack bhaji ×8. No Pedro children (id 3368). '
                 'Please check if GFF369R (Cook Onion Bhaji Mild) applies or use GFF121R.',
                 [(packed['FOBHC3-SK4'], 8, U_UNIT)]),

                ('FOBHC3-B40B', 'BRAKES - Gc - Onion Bhaji 40G x 40 | 1600G', 1600, 1, False,
                 'Frozen Brakes 40×40g bhaji bag. Box OC0010. Pedro id 1508.',
                 [(packed['FOBHC3-BAG40'], 1, U_UNIT), (needed['OC0010'], 1, U_UNIT)]),

                ('FOBHC3-R6T', 'GZFR - Onion Bhaji x 4 | 160G X 6', 960, 6, False,
                 'Frozen Gazebo SK4 4-pack bhaji ×6. Box OC002. Pedro id 2271.',
                 [(packed['FOBHC3-SK4'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('FOBHL-B20B', 'Gc - Onion Bhaji x 20 | 2000G X 1 BAG', 2000, 1, False,
                 'Frozen Gc 20×100g bhaji bag. Box OC001. GFF477SD. Pedro id 1474.',
                 [(packed['FOBHL-BAG20'], 1, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('FOBHL-B20BVE', 'VE - Onion Bhaji 100G x 20 | 2000G X 1', 2000, 1, False,
                 'Frozen VE 20×100g bhaji bag. Box OC001. GFF220SD. Pedro id 1502.',
                 [(packed['FOBHL-BAG20'], 1, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('FOBHL-B20X2B', 'Gc - Onion Bhaji 100G x 20 | 2000G X 2', 4000, 2, False,
                 'Frozen Gc 20×100g bhaji bag ×2. Box OC0016. 107-GFF478SD. Pedro id 1494.',
                 [(packed['FOBHL-BAG20'], 2, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('FOBH1C-B4KGB', 'GC - Apetito Onion Bhaji | 4000G', 4000, 1, False,
                 'GC Apetito 4kg bhaji (GFF009-4R low salt chain, 30g unit). Box OC002. Pedro id 1510. '
                 'Please import GFF009-4R recipe from harvi/1 LR/Signed copy/ before completing this recipe.',
                 [(needed['OC002'], 1, U_UNIT)]),
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
        self.stdout.write(self.style.SUCCESS('onion bhaji chain done'))
