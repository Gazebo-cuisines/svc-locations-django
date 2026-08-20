"""Mexican Chicken Samosa (GFF451R) + Peri-Peri Samosa (GFF299R) chains.

GFF451R-S (29000g ✓): Salsa 12000 + Chipotle 7000 + Hot Sauce 5000 + Sugar 3000 + Salt 1000 + Black Pepper 1000.
GFF451R (241000g, 235000g after omission):
  ANOMALY: Green Diced Chillies (Del Sol) Drained 6000g — no catalogue code. Omitted. Log: add green diced chilli code.

GFF299R-S (800g ✓): Smoked Paprika 280 + Salt 280 + Chilli 40 + Cumin 40 + Coriander 40 + Black Pepper 40 + Cayenne 40 + Nutmeg 40.
GFF299R (44200g ✓).

  python manage.py import_live_mexican_periperi_samosa
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

PDF_MX451 = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF451R - Mexican Chicken Samosa Mix -v.1.pdf'
PDF_SP451 = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF451R-S - Mexican Chicken Samosa Mix - Spices.pdf'
PDF_MX299 = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here' / 'GFF299R - Peri Peri Chicken Parcel Mix v.3.pdf'
PDF_SP299 = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE' / 'GFF299R-S - Peri Peri Chicken Parcel Mix - Spices V.2.pdf'

CAT_SPICE = 164
CAT_BELT = 167
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127

NOTE_SP451 = 'Source: GFF451R-S V.1. PDF total 29000g ✓.'
NOTE_MX451 = ('Source: GFF451R V.1. PDF total 241000g; imported 235000g ✓. '
               'ANOMALY: Green Diced Chillies (Del Sol) Drained 6000g — no catalogue code, omitted. '
               'Please add diced green chilli product and update recipe.')
NOTE_SP299 = 'Source: GFF299R-S V.2. PDF total 800g ✓.'
NOTE_MX299 = 'Source: GFF299R V.3. PDF total 44200g ✓.'


class Command(BaseCommand):
    help = 'Create Mexican & Peri-Peri chicken samosa chains + FG drafts.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'SAUCE0-13', 'SAUCE0-19', 'SAUCE0-29', 'SAUCE0-03', 'SAUCE0-02',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-05', 'SPICE0-08', 'SPICE0-09',
                'SPICE0-24', 'SPICE0-30', 'SPICE0-36', 'SPICE0-55',
                'PROTEIN-08',
                'VEGFRO-02', 'VEGFRO-05', 'VEGFRO-10', 'VEGFRO-11', 'VEGFRO-31', 'VEGFRO-35',
                'INGRAD-01', 'INGRAD-06', 'INGRAD-09',
                'VEGCHI-03',
                'OC003', 'OC007',
            )

            # ══════════════════════════════════════════════════════════════════
            # MEXICAN CHICKEN SAMOSA (GFF451R)
            # ══════════════════════════════════════════════════════════════════

            # ── spice ─────────────────────────────────────────────────────────
            sp451, c = make_product('GFF451R-S', 'Mexican Chicken Samosa - Spices',
                                    CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF451R-S', False, NOTE_SP451)
            results.append(f"{'NEW' if c else 'reuse'} GFF451R-S id={sp451.id}")
            v = make_draft(sp451, NOTE_SP451)
            clear_lines(v)
            add_line(v, 1, needed['SAUCE0-13'], 12000, U_G)
            add_line(v, 2, needed['SAUCE0-19'], 7000, U_G)
            add_line(v, 3, needed['SAUCE0-29'], 5000, U_G)
            add_line(v, 4, needed['SPICE0-03'], 3000, U_G)
            add_line(v, 5, needed['SPICE0-02'], 1000, U_G)
            add_line(v, 6, needed['SPICE0-24'], 1000, U_G)
            v.batch_quantity = Decimal('29000'); v.batch_unit_id = U_G
            v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v, PDF_SP451, 'GFF451R-S V.1 spice evidence')
            sync_has_recipe(sp451.id)

            # ── mixer ─────────────────────────────────────────────────────────
            mx451, c = make_product('GFF451R-Mx', 'Mexican Chicken Samosa - 451 - Mixer',
                                    CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF451R-Mx', False, NOTE_MX451)
            results.append(f"{'NEW' if c else 'reuse'} GFF451R-Mx id={mx451.id}")
            v = make_draft(mx451, NOTE_MX451)
            clear_lines(v)
            add_line(v, 1, needed['PROTEIN-08'], 70000, U_G)
            add_line(v, 2, needed['VEGFRO-02'], 25000, U_G)
            add_line(v, 3, needed['INGRAD-06'], 20000, U_G)
            add_line(v, 4, needed['SAUCE0-03'], 18000, U_G)
            add_line(v, 5, needed['VEGFRO-05'], 15000, U_G)
            add_line(v, 6, needed['VEGFRO-10'], 15000, U_G)
            add_line(v, 7, needed['VEGFRO-31'], 12000, U_G)
            add_line(v, 8, needed['INGRAD-09'], 12000, U_G)
            add_line(v, 9, needed['SAUCE0-02'], 10000, U_G)
            # line 10: Green Diced Chillies 6000g OMITTED — no catalogue code
            add_line(v, 10, needed['VEGFRO-11'], 5000, U_G)
            add_line(v, 11, needed['INGRAD-01'], 4000, U_G)
            add_line(v, 12, sp451, 29000, U_G)
            v.batch_quantity = Decimal('235000'); v.batch_unit_id = U_G
            v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v, PDF_MX451, 'GFF451R V.1 mixer evidence')
            sync_has_recipe(mx451.id)

            # ── belt/fry 100g ─────────────────────────────────────────────────
            b451, c = make_product('GFF451R-B-100', 'Mexican Chicken Samosa 100g - 451 - Portioning',
                                   CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False,
                                   'Pedro source — 100g mexican chicken samosa portioning (GFF451R).')
            v = make_draft(b451, 'GFF451R portioning')
            clear_lines(v)
            add_line(v, 1, mx451, 45, U_G)
            results.append(f"{'NEW' if c else 'reuse'} GFF451R-B-100 id={b451.id}")

            f451, c = make_product('GFF451R - F - 100', 'Mexican Chicken Samosa 100g - 451 - Frying',
                                   CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False,
                                   'Pedro source — 100g mexican chicken samosa frying (GFF451R).')
            v = make_draft(f451, 'GFF451R frying')
            clear_lines(v)
            add_line(v, 1, b451, 1, U_UNIT)
            results.append(f"{'NEW' if c else 'reuse'} GFF451R - F - 100 id={f451.id}")
            sync_has_recipe(b451.id); sync_has_recipe(f451.id)

            # ── packed items ──────────────────────────────────────────────────
            ccmsal, c = make_product('CCMSAL', 'Mexican Chicken Samosa 100g Packed Item',
                                     CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF451R', True,
                                     'Mexican chicken samosa 100g unit (GFF451R). '
                                     'Please confirm tray/film code.')
            results.append(f"{'NEW' if c else 'reuse'} CCMSAL id={ccmsal.id}")
            v = make_draft(ccmsal, 'Mexican chicken samosa packed item')
            clear_lines(v)
            add_line(v, 1, f451, 1, U_UNIT)
            sync_has_recipe(ccmsal.id)

            # ══════════════════════════════════════════════════════════════════
            # PERI PERI CHICKEN SAMOSA (GFF299R)
            # ══════════════════════════════════════════════════════════════════

            # ── spice ─────────────────────────────────────────────────────────
            sp299, c = make_product('GFF299R-S', 'Peri Peri Chicken Samosa - Spices',
                                    CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF299R-S', False, NOTE_SP299)
            results.append(f"{'NEW' if c else 'reuse'} GFF299R-S id={sp299.id}")
            v = make_draft(sp299, NOTE_SP299)
            clear_lines(v)
            add_line(v, 1, needed['SPICE0-30'], 280, U_G)
            add_line(v, 2, needed['SPICE0-02'], 280, U_G)
            add_line(v, 3, needed['SPICE0-08'], 40, U_G)
            add_line(v, 4, needed['SPICE0-05'], 40, U_G)
            add_line(v, 5, needed['SPICE0-09'], 40, U_G)
            add_line(v, 6, needed['SPICE0-24'], 40, U_G)
            add_line(v, 7, needed['SPICE0-36'], 40, U_G)
            add_line(v, 8, needed['SPICE0-55'], 40, U_G)
            v.batch_quantity = Decimal('800'); v.batch_unit_id = U_G
            v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v, PDF_SP299, 'GFF299R-S V.2 spice evidence')
            sync_has_recipe(sp299.id)

            # ── mixer ─────────────────────────────────────────────────────────
            mx299, c = make_product('GFF299R-Mx', 'Peri Peri Chicken Samosa - 299 - Mixer',
                                    CAT_SPICE, U_G, LOC['lwr'], LOC['belt'], 'GFF299R-Mx', False, NOTE_MX299)
            results.append(f"{'NEW' if c else 'reuse'} GFF299R-Mx id={mx299.id}")
            v = make_draft(mx299, NOTE_MX299)
            clear_lines(v)
            add_line(v, 1, needed['PROTEIN-08'], 20000, U_G)
            add_line(v, 2, needed['VEGCHI-03'], 8000, U_G)
            add_line(v, 3, needed['VEGFRO-05'], 8000, U_G)
            add_line(v, 4, needed['SAUCE0-03'], 3200, U_G)
            add_line(v, 5, needed['SAUCE0-02'], 1800, U_G)
            add_line(v, 6, needed['INGRAD-01'], 800, U_G)
            add_line(v, 7, needed['VEGFRO-10'], 800, U_G)
            add_line(v, 8, needed['VEGFRO-35'], 400, U_G)
            add_line(v, 9, needed['VEGFRO-11'], 400, U_G)
            add_line(v, 10, sp299, 800, U_G)
            v.batch_quantity = Decimal('44200'); v.batch_unit_id = U_G
            v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            attach_file(v, PDF_MX299, 'GFF299R V.3 mixer evidence')
            sync_has_recipe(mx299.id)

            # ── belt/fry 100g ─────────────────────────────────────────────────
            b299, c = make_product('GFF299R-B-100', 'Peri Peri Samosa 100g - 299 - Portioning',
                                   CAT_BELT, U_UNIT, LOC['belt'], LOC['fry'], None, False,
                                   'Pedro source — 100g peri-peri chicken samosa portioning (GFF299R).')
            v = make_draft(b299, 'GFF299R portioning')
            clear_lines(v)
            add_line(v, 1, mx299, 45, U_G)
            results.append(f"{'NEW' if c else 'reuse'} GFF299R-B-100 id={b299.id}")

            f299, c = make_product('GFF299R - F - 100', 'Peri Peri Samosa 100g - 299 - Frying',
                                   CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False,
                                   'Pedro source — 100g peri-peri chicken samosa frying (GFF299R).')
            v = make_draft(f299, 'GFF299R frying')
            clear_lines(v)
            add_line(v, 1, b299, 1, U_UNIT)
            results.append(f"{'NEW' if c else 'reuse'} GFF299R - F - 100 id={f299.id}")
            sync_has_recipe(b299.id); sync_has_recipe(f299.id)

            # ── packed items ──────────────────────────────────────────────────
            ccpsal, c = make_product('CCPSAL', 'Peri Peri Chicken Samosa 100g Packed Item',
                                     CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF299R', True,
                                     'Peri-peri chicken samosa 100g unit (GFF299R). '
                                     'Please confirm tray/film code.')
            results.append(f"{'NEW' if c else 'reuse'} CCPSAL id={ccpsal.id}")
            v = make_draft(ccpsal, 'Peri-peri chicken samosa packed item')
            clear_lines(v)
            add_line(v, 1, f299, 1, U_UNIT)
            sync_has_recipe(ccpsal.id)

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('CCMSAL-G6T', 'Gazebo - Mexican Chicken Samosa | 100G X 6', 100, 6, True,
                 'Gazebo G&G Mexican chicken samosa ×6. Box OC007. Pedro id 2131.',
                 [(ccmsal, 6, U_UNIT), (needed['OC007'], 1, U_UNIT)]),

                ('CCMSAL-G12T', 'Gazebo - Mexican Chicken Samosa | 100G X 12', 100, 12, True,
                 'Gazebo G&G Mexican chicken samosa ×12. Box OC003. Pedro id 2136.',
                 [(ccmsal, 12, U_UNIT), (needed['OC003'], 1, U_UNIT)]),

                ('CCPSAL-G6T', 'Gazebo - Peri Peri Chicken Samosa | 100G X 6', 100, 6, True,
                 'Gazebo G&G Peri Peri chicken samosa ×6. Box OC007. Pedro id 2137.',
                 [(ccpsal, 6, U_UNIT), (needed['OC007'], 1, U_UNIT)]),

                ('CCPSAL-G12T', 'Gazebo - Peri Peri Chicken Samosa | 100G X 12', 100, 12, True,
                 'Gazebo G&G Peri Peri chicken samosa ×12. Box OC003. Pedro id 2138.',
                 [(ccpsal, 12, U_UNIT), (needed['OC003'], 1, U_UNIT)]),
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
                    product_id=fg.id, defaults={'shelf_life_days': 14},
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
        self.stdout.write(self.style.SUCCESS('Mexican & Peri-Peri samosa chains done'))
