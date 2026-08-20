"""Veg Spring Roll full chain + all FG variants.

GFF114R V.7 — PDF total 191700g.
GFF109R Sweet Chilli Sauce (13500g) omitted — not in catalogue. Logged in anomaly.
Imported sum: 178200g.

GFF114R-S spice: Plum Sauce 4500 + Kikkoman Soy 4500 + Salt 1125 + Five Spice 450 = 10575g ✓

  python manage.py import_live_veg_spring_roll_chain
"""
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, U_TRAY, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MX = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF114R VEGETABLE SPRING ROLL - V. 7.pdf'
PDF_SP = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF114R-S Vegetable Spring Roll Spices  V.5.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_SP = (
    'Source: GFF114R-S V.5. PDF total 10575g ✓. '
    'Plum Sauce 4500 + Kikkoman Soy 4500 + Salt 1125 + Five Spice 450.'
)
NOTE_MX = (
    'Source: GFF114R V.7. PDF total 191700g. '
    'GFF109R Sweet Chilli Sauce 13500g omitted — not in catalogue. '
    'Please add Sweet Chilli Sauce (GFF109R) catalogue code and re-add line 8. '
    'Imported sum: 178200g.'
)
NOTE_BELT = 'Pedro source — 50g filler per spring roll (belt stage).'
NOTE_FRY = 'Pedro source — passthrough from belt.'
NOTE_CVSR1M = (
    'SK4 4 × 50g veg spring roll (200g). GFF070MC QAS. '
    'Please confirm film and tray code once catalogue codes confirmed.'
)


class Command(BaseCommand):
    help = 'Create veg spring roll chain + FG drafts. Never activates.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'VEGCHI-04', 'VEGFRO-05', 'VEGFRO-64', 'VEGFRO-06', 'VEGCHI-08',
                'VEGFRO-09', 'SPICE0-07', 'VEGFRO-15', 'VEGFRO-11',
                'SAUCE0-14', 'SAUCE0-10', 'SPICE0-02', 'SPICE0-21',
                'PASTRY-01', 'S666-02', 'S775-03', 'OC002', 'OC001',
            )

            # ── spice ─────────────────────────────────────────────────────────
            sp, c = make_product('GFF114R-S', 'Vegetable Spring Roll - Spices',
                                 CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF114R-S', False, NOTE_SP)
            results.append(f"{'NEW' if c else 'reuse'} GFF114R-S id={sp.id}")
            v_sp = make_draft(sp, NOTE_SP)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SAUCE0-14'], 4500, U_G)
            add_line(v_sp, 2, needed['SAUCE0-10'], 4500, U_G)
            add_line(v_sp, 3, needed['SPICE0-02'], 1125, U_G)
            add_line(v_sp, 4, needed['SPICE0-21'], 450, U_G)
            v_sp.batch_quantity = Decimal('10575'); v_sp.batch_unit_id = U_G
            v_sp.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_sp, PDF_SP, 'GFF114R-S V.5 spice evidence')

            # ── mixer ─────────────────────────────────────────────────────────
            mx, c = make_product('GFF114R-Mx', 'Vegetable Spring Roll - 114 - Mixer',
                                 CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF114R-Mx', False, NOTE_MX)
            results.append(f"{'NEW' if c else 'reuse'} GFF114R-Mx id={mx.id}")
            v_mx = make_draft(mx, NOTE_MX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['VEGCHI-04'], 45000, U_G)
            add_line(v_mx, 2, needed['VEGFRO-05'], 33750, U_G)
            add_line(v_mx, 3, needed['VEGFRO-64'], 31500, U_G)
            add_line(v_mx, 4, needed['VEGFRO-06'], 22500, U_G)
            add_line(v_mx, 5, needed['VEGCHI-08'], 13500, U_G)
            add_line(v_mx, 6, needed['VEGFRO-09'], 13500, U_G)
            add_line(v_mx, 7, needed['SPICE0-07'], 4500, U_G)
            # line 8 = GFF109R sweet chilli sauce 13500g OMITTED
            add_line(v_mx, 9, needed['VEGFRO-15'], 2250, U_G)
            add_line(v_mx, 10, needed['VEGFRO-11'], 1125, U_G)
            add_line(v_mx, 11, sp, 10575, U_G)
            v_mx.batch_quantity = Decimal('178200'); v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_mx, PDF_MX, 'GFF114R V.7 mixer evidence')

            # ── belt / fry ────────────────────────────────────────────────────
            belt, c = make_product('GFF114R-B-50', 'Vegetable Spring Roll - 50g - 114 - Belt',
                                   CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_BELT)
            results.append(f"{'NEW' if c else 'reuse'} GFF114R-B-50 id={belt.id}")
            v_b = make_draft(belt, NOTE_BELT)
            clear_lines(v_b)
            add_line(v_b, 1, mx, '49.5', U_G)
            add_line(v_b, 2, needed['PASTRY-01'], 1, U_UNIT)

            fry, c = make_product('GFF114R - F - 50', 'Vegetable Spring Roll - 50g - 114 - Frying',
                                  CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_FRY)
            results.append(f"{'NEW' if c else 'reuse'} GFF114R - F - 50 id={fry.id}")
            v_f = make_draft(fry, NOTE_FRY)
            clear_lines(v_f)
            add_line(v_f, 1, belt, 1, U_UNIT)

            # ── CVSR1M packed item ────────────────────────────────────────────
            cvsr1m, c = make_product('CVSR1M', 'SK4 - 4 x Vegetable Spring Roll - 114R - 50g | 200g',
                                     CAT_PACKED, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF070MC', True, NOTE_CVSR1M)
            results.append(f"{'NEW' if c else 'reuse'} CVSR1M id={cvsr1m.id}")
            v_hr = make_draft(cvsr1m, NOTE_CVSR1M)
            clear_lines(v_hr)
            add_line(v_hr, 1, fry, 4, U_UNIT)
            ProductPackaging.objects.update_or_create(
                product_id=cvsr1m.id, defaults={'pack_weight': Decimal('200')},
            )

            # ── bulk packed items ─────────────────────────────────────────────
            NOTE_CVSRL10 = ('10 × 85g veg spring roll tray (850g). GFF114R chain. '
                            'Please add tray/box code once confirmed.')
            NOTE_CVSRL20 = ('20 × 85g veg spring roll tray (1700g). GFF114R chain. '
                            'Please add tray/box code once confirmed.')
            NOTE_FVSRL_BAG = ('Frozen bag 20 × 85g veg spring roll (1700g). GFF114R chain. '
                              'Please add bag code once confirmed.')

            cvsrl10, c = make_product('CVSRL10', 'LARGE - 10 x Vegetable Spring Roll - 114R - 85g | 850g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, True, NOTE_CVSRL10)
            results.append(f"{'NEW' if c else 'reuse'} CVSRL10 id={cvsrl10.id}")

            cvsrl20, c = make_product('CVSRL20', 'LARGE - 20 x Vegetable Spring Roll - 114R - 85g | 1700g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, True, NOTE_CVSRL20)
            results.append(f"{'NEW' if c else 'reuse'} CVSRL20 id={cvsrl20.id}")

            fvsrl_bag20, c = make_product('FVSRL-BAG20', 'BAG - 20 x Vegetable Spring Roll - 114R - 85g | 1700g',
                                          CAT_PACKED, U_UNIT, LOC['fry'], LOC['dispatch'], None, False, NOTE_FVSRL_BAG)
            results.append(f"{'NEW' if c else 'reuse'} FVSRL-BAG20 id={fvsrl_bag20.id}")

            for p in (cvsr1m, cvsrl10, cvsrl20, fvsrl_bag20):
                if p != cvsr1m:
                    make_draft(p, p.remarks)
                    clear_lines(p.recipe.versions.order_by('version_number').first())
                sync_has_recipe(p.id)

            for p in (sp, mx, belt, fry):
                sync_has_recipe(p.id)

            # ── FG variants ───────────────────────────────────────────────────
            fg_specs = [
                ('CVSR1M-R6TEB', 'Booths Snacks - Vegetable Spring Roll | 200G X 6', 1200, 6, True,
                 'Booths 4-pack veg spring roll ×6. Sleeve S666-02, box OC002. Pedro id 1246.',
                 [(cvsr1m, 6, U_UNIT), (needed['S666-02'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('CVSR2M-1R6TSP', 'SP - Vegetable Spring Rolls x 4 | 200G X 6', 1200, 6, True,
                 'SP 4-pack veg spring roll ×6. Sleeve S775-03, box OC002. Pedro id 1284.',
                 [(cvsr1m, 6, U_UNIT), (needed['S775-03'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('CVSRL-B10T', 'Gc - Vegetable Spring Roll x 10 | 850G X 1', 850, 1, True,
                 'Gc 10 × 85g veg spring roll. No box in Pedro. Pedro id 1454.',
                 [(cvsrl10, 1, U_UNIT)]),

                ('CVSRL-B10X2T', 'Gc - Vegetable Spring Roll x 10 | 850G X 2', 1700, 2, True,
                 'Gc 10 × 85g veg spring roll × 2. Box OC001. Pedro id 1458.',
                 [(cvsrl10, 2, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('CVSRL-B20T', 'Gc - Vegetable Spring Roll x 20 | 1700G X 1', 1700, 1, True,
                 'Gc 20 × 85g veg spring roll. No box in Pedro. Pedro id 1468.',
                 [(cvsrl20, 1, U_UNIT)]),

                ('FVSRL-B20B', 'Gc - Vegetable Spring Roll x 20 | 1700G X 1 BAG', 1700, 1, False,
                 'Frozen Gc 20 × 85g veg spring roll bag. Box OC001. Pedro id 1478.',
                 [(fvsrl_bag20, 1, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('FVSRM-R6T', 'GZFR - Vegetable Spring Rolls x 4 | 200G X 6', 1200, 6, False,
                 'Frozen Gazebo 4-pack veg spring roll ×6. Box OC002. Pedro id 2270.',
                 [(cvsr1m, 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),
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
        self.stdout.write(self.style.SUCCESS('veg spring roll chain done'))
