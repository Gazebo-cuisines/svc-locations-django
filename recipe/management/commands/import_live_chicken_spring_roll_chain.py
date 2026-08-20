"""Chicken Spring Roll GFF007R chain + all FG variants.

GFF007R-S V.14 — PDF total 974g ✓ (449+205+192+128).
GFF007R V.14 — PDF total 32103g ✓ (7949+5128+2500+2500+3590+2564+2564+1538+1282+1282+205+26+974).

Soya sauce mapped to SAUCE0-35 (SOY SAUCE LIGHT).
Hot Pepper mapped to SAUCE0-29 (HOT SAUCE).
Peppers: FRESH 20MM diced (VEGCHI-24 green, VEGCHI-13 red).
Spring Onions: VEGCHI-28 (20MM fresh).

Duck spring rolls (CDSR1M-R6TEB, FDSRM-B25B) included as empty drafts —
GFF281R-282R duck chain PDFs exist but are a separate recipe chain. Notes added.

  python manage.py import_live_chicken_spring_roll_chain
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

PDF_MX = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF007R CHICKEN SPRING ROLL - V.14.pdf'
PDF_SP = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF007R-S CHICKEN SPRING ROLL - SPICES V.14.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_SP = 'Source: GFF007R-S V.14. PDF total 974g ✓.'
NOTE_MX = ('Source: GFF007R V.14. PDF total 32103g ✓. '
            'Bean sprouts 7949 + Cabbage 5128 + Cooked Diced Chicken 2500 + '
            'Chicken Fines 2500 + Carrot 3590 + Bamboo 2564 + Water Chestnut 2564 + '
            'Spring Onion 1538 + Green Pepper 1282 + Red Pepper 1282 + '
            'Ginger 205 + Garlic 26 + Spice 974.')
NOTE_BELT_50 = 'Pedro source — 50g chicken spring roll portioning (GFF007R chain).'
NOTE_BELT_30 = 'Pedro source — 30g chicken spring roll portioning (GFF007R chain).'
NOTE_FRY_50 = 'Pedro source — 50g chicken spring roll frying (GFF007R chain).'
NOTE_FRY_30 = 'Pedro source — 30g chicken spring roll frying (GFF007R chain).'


class Command(BaseCommand):
    help = 'Create chicken spring roll GFF007R chain + FG drafts.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'VEGCHI-06', 'VEGCHI-08', 'VEGCHI-04',
                'INGRAD-14', 'VEGFRO-09', 'VEGCHI-28',
                'VEGCHI-24', 'VEGCHI-13',
                'VEGFRO-15', 'VEGFRO-11',
                'PROTEIN-02', 'PROTEIN-12',
                'SAUCE0-35', 'SAUCE0-29', 'SPICE0-02', 'SPICE0-35',
                'OC007', 'OC002', 'OC001', 'OC0016', 'OC008',
                'S666-04',
            )

            # ── spice ─────────────────────────────────────────────────────────
            sp, c = make_product('GFF007R-S', 'Chicken Spring Roll - Spices',
                                 CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF007R-S', False, NOTE_SP)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R-S id={sp.id}")
            v_sp = make_draft(sp, NOTE_SP)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SAUCE0-35'], 449, U_G)
            add_line(v_sp, 2, needed['SAUCE0-29'], 205, U_G)
            add_line(v_sp, 3, needed['SPICE0-02'], 192, U_G)
            add_line(v_sp, 4, needed['SPICE0-35'], 128, U_G)
            v_sp.batch_quantity = Decimal('974'); v_sp.batch_unit_id = U_G
            v_sp.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_sp, PDF_SP, 'GFF007R-S V.14 spice evidence')
            sync_has_recipe(sp.id)

            # ── mixer ─────────────────────────────────────────────────────────
            mx, c = make_product('GFF007R-Mx', 'Chicken Spring Roll - 007 - Mixer',
                                 CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF007R-Mx', False, NOTE_MX)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R-Mx id={mx.id}")
            v_mx = make_draft(mx, NOTE_MX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['VEGCHI-06'], 7949, U_G)
            add_line(v_mx, 2, needed['VEGCHI-08'], 5128, U_G)
            add_line(v_mx, 3, needed['PROTEIN-02'], 2500, U_G)
            add_line(v_mx, 4, needed['PROTEIN-12'], 2500, U_G)
            add_line(v_mx, 5, needed['VEGCHI-04'], 3590, U_G)
            add_line(v_mx, 6, needed['INGRAD-14'], 2564, U_G)
            add_line(v_mx, 7, needed['VEGFRO-09'], 2564, U_G)
            add_line(v_mx, 8, needed['VEGCHI-28'], 1538, U_G)
            add_line(v_mx, 9, needed['VEGCHI-24'], 1282, U_G)
            add_line(v_mx, 10, needed['VEGCHI-13'], 1282, U_G)
            add_line(v_mx, 11, needed['VEGFRO-15'], 205, U_G)
            add_line(v_mx, 12, needed['VEGFRO-11'], 26, U_G)
            add_line(v_mx, 13, sp, 974, U_G)
            v_mx.batch_quantity = Decimal('32102'); v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v_mx, PDF_MX, 'GFF007R V.14 mixer evidence')
            sync_has_recipe(mx.id)

            # ── belt/fry 50g ──────────────────────────────────────────────────
            b50, c = make_product('GFF007R-B-50', 'Chicken Spring Roll 50g - 007 - Portioning',
                                  CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_BELT_50)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R-B-50 id={b50.id}")
            v_b50 = make_draft(b50, NOTE_BELT_50)
            clear_lines(v_b50)
            add_line(v_b50, 1, mx, 48, U_G)

            f50, c = make_product('GFF007R - F - 50', 'Chicken Spring Roll 50g - 007 - Frying',
                                  CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_FRY_50)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R - F - 50 id={f50.id}")
            v_f50 = make_draft(f50, NOTE_FRY_50)
            clear_lines(v_f50)
            add_line(v_f50, 1, b50, 1, U_UNIT)
            sync_has_recipe(b50.id); sync_has_recipe(f50.id)

            # ── belt/fry 30g ──────────────────────────────────────────────────
            b30, c = make_product('GFF007R-B-30', 'Chicken Spring Roll 30g - 007 - Portioning',
                                  CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_BELT_30)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R-B-30 id={b30.id}")
            v_b30 = make_draft(b30, NOTE_BELT_30)
            clear_lines(v_b30)
            add_line(v_b30, 1, mx, 28, U_G)

            f30, c = make_product('GFF007R - F - 30', 'Chicken Spring Roll 30g - 007 - Frying',
                                  CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_FRY_30)
            results.append(f"{'NEW' if c else 'reuse'} GFF007R - F - 30 id={f30.id}")
            v_f30 = make_draft(f30, NOTE_FRY_30)
            clear_lines(v_f30)
            add_line(v_f30, 1, b30, 1, U_UNIT)
            sync_has_recipe(b30.id); sync_has_recipe(f30.id)

            # ── packed items ──────────────────────────────────────────────────
            ccsrm, c = make_product('CCSRM', 'GFF007R - 4 x Chicken Spring Roll 50g | 200g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF007R-Mx', False,
                                    'Chilled 50g chicken spring roll tray (4 units, GFF007R). '
                                    'Please confirm tray/film code and build recipe from GFF007R PDF.')
            results.append(f"{'NEW' if c else 'reuse'} CCSRM id={ccsrm.id}")
            v_ccsrm = make_draft(ccsrm, 'GFF007R 50g packed item.')
            clear_lines(v_ccsrm)
            add_line(v_ccsrm, 1, f50, 4, U_UNIT)
            sync_has_recipe(ccsrm.id)

            ccsrc, c = make_product('CCSRC', 'GFF007R - Chicken Spring Roll 30g | 30g',
                                    CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, False,
                                    '30g chicken spring roll unit (GFF007R). '
                                    'Please confirm tray/film code and build recipe.')
            results.append(f"{'NEW' if c else 'reuse'} CCSRC id={ccsrc.id}")
            v_ccsrc = make_draft(ccsrc, 'GFF007R 30g packed item.')
            clear_lines(v_ccsrc)
            add_line(v_ccsrc, 1, f30, 1, U_UNIT)
            sync_has_recipe(ccsrc.id)

            # Duck spring roll packed items (GFF282R chain — separate PDFs available)
            duck_note = ('Duck spring roll unit. GFF282R chain PDFs exist at harvi/1 LR/Signed copy/'
                         'GFF281R-Duck Parcel Filling V.2 and GFF282R-Duck Parcel V.3. '
                         'Please import GFF282R chain then complete this recipe.')
            cdsr1m, c = make_product('CDSR1M', 'GFF282R - 4 x Duck Spring Roll | 50g',
                                     CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], None, False, duck_note)
            results.append(f"{'NEW' if c else 'reuse'} CDSR1M id={cdsr1m.id}")
            v_cdsr1m = make_draft(cdsr1m, duck_note)
            clear_lines(v_cdsr1m)
            sync_has_recipe(cdsr1m.id)

            fdsrm, c = make_product('FDSRM', 'GFF282R - Duck Spring Roll 50g bulk bag',
                                    CAT_PACKED, U_UNIT, LOC['dispatch'], LOC['dispatch'], None, False,
                                    duck_note)
            results.append(f"{'NEW' if c else 'reuse'} FDSRM id={fdsrm.id}")
            v_fdsrm = make_draft(fdsrm, duck_note)
            clear_lines(v_fdsrm)
            sync_has_recipe(fdsrm.id)

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('CCSRM-R6TTA', 'TA - Chicken Spring Roll x 4 | 200G X 6', 200, 6, True,
                 'TA 50g chicken spring roll ×4 ×6. No OC identified in Pedro (id 1290).',
                 [(ccsrm, 6, U_UNIT)]),

                ('CCSRM-B15X4T', 'GC - Chicken Spring Roll 50G x 15 | 750G X 4', 750, 4, True,
                 'GC 50g chicken spring roll ×15 ×4. Box OC0016. Pedro id 2180.',
                 [(ccsrm, 60, U_UNIT), (needed['OC0016'], 1, U_UNIT)]),

                ('CCSRC1-B25X4TTA', 'TA - Chicken Spring Roll 30G with Sweet Chilli Dip x 25 | 870G x 4',
                 870, 4, True,
                 'TA 30g CSR ×25 with sweet chilli dip ×4. Box OC008. Pedro id 2564. '
                 'Sweet chilli dip not mapped — please add dip product and update recipe.',
                 [(ccsrc, 100, U_UNIT), (needed['OC008'], 1, U_UNIT)]),

                ('CCSRC2-B30X8T', 'COSTCO - GC - 30 Chicken Spring Roll 30G | 900G X 8', 900, 8, True,
                 'COSTCO 30g CSR ×30 ×8. Box OC001. Pedro id 3431.',
                 [(ccsrc, 240, U_UNIT), (needed['OC001'], 1, U_UNIT)]),

                ('CDSR1M-R6TEB', 'Booths Snacks - Duck Spring Rolls x 4 | 200G X 6', 200, 6, True,
                 'Booths 50g duck spring roll ×4 ×6. Sleeve S666-04, box OC002. Pedro id 1243. '
                 'Recipe left empty pending GFF282R duck chain import.',
                 [(cdsr1m, 6, U_UNIT), (needed['S666-04'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('FDSRM-B25B', 'Gc - Duck Springroll x 25 | 1250G X 1', 1250, 1, False,
                 'Frozen Gc 25×50g duck spring roll bag. Box OC002. Pedro id 1470. '
                 'Recipe left empty pending GFF282R duck chain import.',
                 [(fdsrm, 1, U_UNIT), (needed['OC002'], 1, U_UNIT)]),
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
        self.stdout.write(self.style.SUCCESS('chicken spring roll chain done'))
