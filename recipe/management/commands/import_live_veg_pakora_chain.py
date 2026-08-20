"""Vegetable Pakora GFF122R chain + all FG variants.

GFF122R-S V.4 — PDF total 2145g ✓ (900+450+300+300+75+75+45).
GFF122R V.4 — PDF total 122145g ✓ (52500+22500+21000+12000+7500+4500+2145).

CVPKL-B20T uses 100g pakoras — no matching PDF (GFF122R is 30g only).
  Left as empty draft with note.

  python manage.py import_live_veg_pakora_chain
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Product, ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MX = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF122R - VEGETABLE PAKORA 30G (GFF122R) -v.4.pdf'
PDF_SP = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF122R-S - Vegetable Pakora 30g ( GFF122R-S)- Spices V.4.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_SP = 'Source: GFF122R-S V.4. PDF total 2145g ✓.'
NOTE_MX = ('Source: GFF122R V.4. PDF total 122145g ✓. '
            'Sliced Onions 52500 + Grated Potato 22500 + Gram Flour 21000 + '
            'Peas 12000 + Shredded Carrot 7500 + Fresh Coriander 4500 + Spice 2145.')


class Command(BaseCommand):
    help = 'Create veg pakora GFF122R chain + FG drafts.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'VEGCHI-01', 'VEGCHI-07', 'INGRAD-02', 'VEGFRO-01',
                'VEGCHI-04', 'VEGCHI-09',
                'SPICE0-02', 'SPICE0-04', 'SPICE0-06', 'SPICE0-08',
                'SPICE0-11', 'SPICE0-16', 'SPICE0-25',
                'S666-04', 'OC002', 'OC0016',
            )

            # ── spice ─────────────────────────────────────────────────────────
            sp, c = make_product('GFF122R-S', 'Vegetable Pakora 30g - Spices',
                                 CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF122R-S', False, NOTE_SP)
            results.append(f"{'NEW' if c else 'reuse'} GFF122R-S id={sp.id}")
            v_sp = make_draft(sp, NOTE_SP)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SPICE0-02'], 900, U_G)
            add_line(v_sp, 2, needed['SPICE0-04'], 450, U_G)
            add_line(v_sp, 3, needed['SPICE0-06'], 300, U_G)
            add_line(v_sp, 4, needed['SPICE0-16'], 300, U_G)
            add_line(v_sp, 5, needed['SPICE0-11'], 75, U_G)
            add_line(v_sp, 6, needed['SPICE0-25'], 75, U_G)
            add_line(v_sp, 7, needed['SPICE0-08'], 45, U_G)
            v_sp.batch_quantity = Decimal('2145'); v_sp.batch_unit_id = U_G
            v_sp.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_sp, PDF_SP, 'GFF122R-S V.4 spice evidence')
            sync_has_recipe(sp.id)

            # ── mixer ─────────────────────────────────────────────────────────
            mx, c = make_product('GFF122R-Mx', 'Vegetable Pakora 30g - 122 - Mixer',
                                 CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF122R-Mx', False, NOTE_MX)
            results.append(f"{'NEW' if c else 'reuse'} GFF122R-Mx id={mx.id}")
            v_mx = make_draft(mx, NOTE_MX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['VEGCHI-01'], 52500, U_G)
            add_line(v_mx, 2, needed['VEGCHI-07'], 22500, U_G)
            add_line(v_mx, 3, needed['INGRAD-02'], 21000, U_G)
            add_line(v_mx, 4, needed['VEGFRO-01'], 12000, U_G)
            add_line(v_mx, 5, needed['VEGCHI-04'], 7500, U_G)
            add_line(v_mx, 6, needed['VEGCHI-09'], 4500, U_G)
            add_line(v_mx, 7, sp, 2145, U_G)
            v_mx.batch_quantity = Decimal('122145'); v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_mx, PDF_MX, 'GFF122R V.4 mixer evidence')
            sync_has_recipe(mx.id)

            # ── belt/fry 30g ──────────────────────────────────────────────────
            b30, c = make_product('GFF122R-B-30', 'Vegetable Pakora 30g - 122 - Portioning',
                                  CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False,
                                  'Pedro source — 30g veg pakora portioning (GFF122R).')
            results.append(f"{'NEW' if c else 'reuse'} GFF122R-B-30 id={b30.id}")
            v_b = make_draft(b30, 'GFF122R portioning')
            clear_lines(v_b)
            add_line(v_b, 1, mx, 29, U_G)

            f30, c = make_product('GFF122R - F - 30', 'Vegetable Pakora 30g - 122 - Frying',
                                  CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False,
                                  'Pedro source — 30g veg pakora frying (GFF122R).')
            results.append(f"{'NEW' if c else 'reuse'} GFF122R - F - 30 id={f30.id}")
            v_f = make_draft(f30, 'GFF122R frying')
            clear_lines(v_f)
            add_line(v_f, 1, b30, 1, U_UNIT)
            sync_has_recipe(b30.id); sync_has_recipe(f30.id)

            # ── packed items ──────────────────────────────────────────────────
            cvpkc, c = make_product('CVPKC', 'GFF122R - 6 x Vegetable Pakora 30g | 180g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True,
                                    'Chilled 30g veg pakora tray (6 units, GFF122R). '
                                    'Please confirm tray/film code.')
            results.append(f"{'NEW' if c else 'reuse'} CVPKC id={cvpkc.id}")
            v_cvpkc = make_draft(cvpkc, 'GFF122R 30g 6-unit packed item')
            clear_lines(v_cvpkc)
            add_line(v_cvpkc, 1, f30, 6, U_UNIT)
            sync_has_recipe(cvpkc.id)

            cvpkc20, c = make_product('CVPKC20', 'GFF122R - 20 x Vegetable Pakora 30g | 600g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True,
                                      '20 × 30g veg pakora bulk tray (GFF122R). '
                                      'Please confirm tray code.')
            results.append(f"{'NEW' if c else 'reuse'} CVPKC20 id={cvpkc20.id}")
            v_c20 = make_draft(cvpkc20, 'GFF122R 30g 20-unit packed item')
            clear_lines(v_c20)
            add_line(v_c20, 1, f30, 20, U_UNIT)
            sync_has_recipe(cvpkc20.id)

            # CVPKL (100g large pakora) — no GFF122R match, different recipe needed
            cvpkl_note = ('Large 100g vegetable pakora bulk item (20 × 100g = 2000g). '
                          'GFF122R is for 30g only. Please identify the correct recipe PDF for 100g pakora '
                          'and build from scratch. Pedro id 1465.')
            cvpkl, c = make_product('CVPKL', 'Vegetable Pakora 100g Bulk Item (TBC)',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, True, cvpkl_note)
            results.append(f"{'NEW' if c else 'reuse'} CVPKL id={cvpkl.id}")
            make_draft(cvpkl, cvpkl_note)
            sync_has_recipe(cvpkl.id)

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('CVPK2C-R6TEB', 'Booths Snacks - Vegetable Pakora | 180G X 6', 180, 6, True,
                 'Booths 30g veg pakora ×6 × 6 packs. Sleeve S666-04, box OC002. Pedro id 1247.',
                 [(cvpkc, 6, U_UNIT), (needed['S666-04'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('CVPKL-B20T', 'Gc - Vegetable Pakora x 20 | 2000G X 1', 2000, 1, True,
                 'Gc 20 × 100g veg pakora bulk tray. Pedro id 1465. '
                 'Recipe left empty — 100g pakora chain not yet imported. '
                 'Please add CVPKL recipe once large pakora chain is identified.',
                 [(cvpkl, 1, U_UNIT)]),

                ('FVPKC-B20X4T', 'GC - Vegetable Pakora 30G x 20 | 600G X 4', 600, 4, False,
                 'Frozen GC 30g veg pakora ×20 ×4. Box OC0016. Pedro id 2484.',
                 [(cvpkc20, 4, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),
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
        self.stdout.write(self.style.SUCCESS('veg pakora chain done'))
