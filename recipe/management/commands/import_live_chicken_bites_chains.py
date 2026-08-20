"""Chicken Thigh Bites chains: Pakora (GFF479R), Katsu (GFF480R), Salt & Pepper (GFF481R).

GFF479R-S (2800g ✓). GFF479R (124800g after omitting Water 2000g).
GFF480R-S (57500g ✓). GFF480R (165000g after omitting Water 5000g).
GFF481R-S (57120g ✓). GFF481R (163620g after omitting Water 5000g).

ANOMALY (all three): Meritena 100 (Starch) mapped to SPICE0-59 (STARCH NOVATION ENDURA 0100) — confirm match.
ANOMALY GFF479R: Water 2000g omitted — no catalogue code.
ANOMALY GFF480R: Water 5000g omitted — no catalogue code.
ANOMALY GFF481R: Water 5000g omitted; Garlic Granules mapped to SPICE0-22 (GARLIC POWDER) — confirm match.
ANOMALY GFF480R: Breadcrumbs mapped to INGRAD-76 (PANKO) — confirm if this is the correct type.
ANOMALY GFF481R: Breadcrumbs mapped to INGRAD-76 (PANKO) — confirm.

  python manage.py import_live_chicken_bites_chains
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

SPICE_DIR = DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
MX_DIR = DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'

CAT_SPICE = 164
CAT_FRY = 173
CAT_PACKED = 76
CAT_FG = 127


class Command(BaseCommand):
    help = 'Create chicken bites chains (pakora, katsu, S&P) + FG drafts.'

    def handle(self, *args, **options):
        results = []
        with transaction.atomic():
            needed = require_products(
                'PROTEIN-36', 'VEGFRO-10', 'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-28', 'VEGFRO-44',
                'INGRAD-01', 'INGRAD-02', 'INGRAD-20', 'INGRAD-76',
                'SAUCE0-10', 'SAUCE0-12',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-08', 'SPICE0-09', 'SPICE0-11',
                'SPICE0-12', 'SPICE0-15', 'SPICE0-21', 'SPICE0-22', 'SPICE0-23',
                'SPICE0-24', 'SPICE0-53', 'SPICE0-59',
                'S666-04', 'OC002',
            )

            def make_bites_chain(gff, name_short, sp_ingredients, mx_ingredients,
                                 sp_total, mx_total, sp_pdf, mx_pdf, anomaly_note=''):
                sp_note = f'Source: {gff}-S. PDF total {sp_total}g ✓.{" " + anomaly_note if anomaly_note else ""}'
                mx_note = f'Source: {gff}. PDF total {mx_total + (2000 if gff == "GFF479R" else 5000)}g; {mx_total}g after water omission. {anomaly_note}'

                sp, c = make_product(f'{gff}-S', f'{name_short} - Spices',
                                     CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], f'{gff}-S', False, sp_note)
                results.append(f"{'NEW' if c else 'reuse'} {gff}-S id={sp.id}")
                v = make_draft(sp, sp_note)
                clear_lines(v)
                for i, (prod, qty) in enumerate(sp_ingredients, 1):
                    add_line(v, i, prod, qty, U_G)
                v.batch_quantity = Decimal(str(sp_total)); v.batch_unit_id = U_G
                v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
                if sp_pdf.exists():
                    attach_file(v, sp_pdf, f'{gff}-S spice evidence')
                sync_has_recipe(sp.id)

                mx, c = make_product(f'{gff}-Mx', f'{name_short} - {gff[-3:]} - Marination',
                                     CAT_SPICE, U_G, LOC['lwr'], LOC['fry'], f'{gff}-Mx', False, mx_note)
                results.append(f"{'NEW' if c else 'reuse'} {gff}-Mx id={mx.id}")
                v = make_draft(mx, mx_note)
                clear_lines(v)
                for i, (prod, qty) in enumerate(mx_ingredients, 1):
                    add_line(v, i, prod, qty, U_G)
                add_line(v, len(mx_ingredients) + 1, sp, sp_total, U_G)
                v.batch_quantity = Decimal(str(mx_total)); v.batch_unit_id = U_G
                v.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
                if mx_pdf.exists():
                    attach_file(v, mx_pdf, f'{gff} mixer evidence')
                sync_has_recipe(mx.id)

                fr, c = make_product(f'{gff} - F', f'{name_short} - {gff[-3:]} - Frying',
                                     CAT_FRY, U_UNIT, LOC['fry'], LOC['hr'], None, False,
                                     f'Pedro source — {name_short.lower()} frying ({gff}).')
                results.append(f"{'NEW' if c else 'reuse'} {gff} - F id={fr.id}")
                v = make_draft(fr, f'{gff} frying')
                clear_lines(v)
                add_line(v, 1, mx, 1, U_UNIT)
                sync_has_recipe(fr.id)

                return sp, mx, fr

            # ── GFF479R Pakora Bites ───────────────────────────────────────────
            sp479_ingr = [
                (needed['SPICE0-08'], 600), (needed['SPICE0-02'], 600),
                (needed['SPICE0-15'], 500), (needed['SPICE0-09'], 400),
                (needed['VEGFRO-44'], 400), (needed['SPICE0-53'], 200),
                (needed['SPICE0-11'], 100),
            ]
            mx479_ingr = [
                (needed['PROTEIN-36'], 100000), (needed['INGRAD-02'], 10000),
                (needed['SAUCE0-12'], 8000),   # Water 2000g omitted
                (needed['VEGFRO-11'], 2000), (needed['VEGFRO-10'], 2000),
            ]
            _, mx479, fr479 = make_bites_chain(
                'GFF479R', 'Chicken Thigh Pakora Bites', sp479_ingr, mx479_ingr,
                2800, 124800,
                SPICE_DIR / 'GFF479R-S-Chicken Thigh Pakora Bites - Spices.pdf',
                MX_DIR / 'GFF479R - Chicken Thigh Pakora Bites.pdf',
                'ANOMALY: Water 2000g omitted (no catalogue code).',
            )

            # ── GFF480R Katsu Bites ────────────────────────────────────────────
            sp480_ingr = [
                (needed['INGRAD-76'], 50000), (needed['INGRAD-20'], 2500),
                (needed['SAUCE0-10'], 2000), (needed['SPICE0-23'], 2000),
                (needed['SPICE0-12'], 1000),
            ]
            mx480_ingr = [
                (needed['PROTEIN-36'], 100000),
                # Water 5000g omitted
                (needed['VEGFRO-15'], 3000), (needed['SPICE0-59'], 2500),
                (needed['SAUCE0-12'], 2000),
            ]
            _, mx480, fr480 = make_bites_chain(
                'GFF480R', 'Chicken Thigh Katsu Bites', sp480_ingr, mx480_ingr,
                57500, 165000,
                SPICE_DIR / 'GFF480R-S-Chicken Thigh Katsu Bites - Spices.pdf',
                MX_DIR / 'GFF480R - Chicken Thigh Katsu Bites.pdf',
                'ANOMALY: Water 5000g omitted. Meritena 100 mapped to SPICE0-59 — confirm.',
            )

            # ── GFF481R Salt & Pepper Bites ───────────────────────────────────
            sp481_ingr = [
                (needed['INGRAD-76'], 50000), (needed['SAUCE0-10'], 2800),
                (needed['INGRAD-20'], 2500), (needed['SPICE0-24'], 500),
                (needed['SPICE0-22'], 500),  # garlic granules → garlic powder (anomaly)
                (needed['SPICE0-03'], 500), (needed['SPICE0-21'], 300),
                (needed['SPICE0-02'], 20),
            ]
            mx481_ingr = [
                (needed['PROTEIN-36'], 100000),
                # Water 5000g omitted
                (needed['SPICE0-59'], 2500), (needed['VEGFRO-28'], 2000),
                (needed['SAUCE0-12'], 2000),
            ]
            _, mx481, fr481 = make_bites_chain(
                'GFF481R', 'Chicken Thigh Salt & Pepper Bites', sp481_ingr, mx481_ingr,
                57120, 163620,
                SPICE_DIR / 'GFF481R-S-Chicken Thigh Salt & Pepper Bites - Spices.pdf',
                MX_DIR / 'GFF481R - Chicken Thigh Salt & Pepper Bites.pdf',
                'ANOMALY: Water 5000g omitted. Garlic Granules mapped to SPICE0-22 (GARLIC POWDER) — confirm.',
            )

            # ── packed items ──────────────────────────────────────────────────
            pkr_note = 'Chicken thigh pakora bites 130g pack (GFF479R). Please confirm tray/film code.'
            kts_note = 'Chicken thigh katsu bites 130g pack (GFF480R). Please confirm tray/film code.'
            sp_note = 'Chicken thigh S&P bites 130g pack (GFF481R). Please confirm tray/film code.'

            ccpkc3, c = make_product('CCPKC3', 'Chicken Thigh Pakora Bites 130g',
                                     CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF479R', True, pkr_note)
            results.append(f"{'NEW' if c else 'reuse'} CCPKC3 id={ccpkc3.id}")
            v = make_draft(ccpkc3, pkr_note)
            clear_lines(v)
            add_line(v, 1, fr479, 1, U_UNIT)
            sync_has_recipe(ccpkc3.id)

            cckatb1, c = make_product('CCKATB1', 'Chicken Thigh Katsu Bites 130g',
                                      CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF480R', True, kts_note)
            results.append(f"{'NEW' if c else 'reuse'} CCKATB1 id={cckatb1.id}")
            v = make_draft(cckatb1, kts_note)
            clear_lines(v)
            add_line(v, 1, fr480, 1, U_UNIT)
            sync_has_recipe(cckatb1.id)

            ccspb1, c = make_product('CCSPB1', 'Chicken Thigh Salt & Pepper Bites 130g',
                                     CAT_PACKED, U_UNIT, LOC['fry'], LOC['hr'], 'GFF481R', True, sp_note)
            results.append(f"{'NEW' if c else 'reuse'} CCSPB1 id={ccspb1.id}")
            v = make_draft(ccspb1, sp_note)
            clear_lines(v)
            add_line(v, 1, fr481, 1, U_UNIT)
            sync_has_recipe(ccspb1.id)

            # ── FG SKUs ───────────────────────────────────────────────────────
            fg_specs = [
                ('CCPKC3-R6TTA', 'TA - Chicken Pakora Bites | 130G X 6', 130, 6, True,
                 'TA 130g chicken pakora bites ×6. GFF479R. Pedro id 2353.',
                 [(ccpkc3, 6, U_UNIT)]),

                ('CCKATB1-R6TTA', 'TA - Katsu Fried Chicken Bites | 130G X 6', 130, 6, True,
                 'TA 130g katsu chicken bites ×6. GFF480R. Pedro id 2354.',
                 [(cckatb1, 6, U_UNIT)]),

                ('CCSPB1-R6TTA', 'TA - Salt & Pepper Chicken Bites | 130G X 6', 130, 6, True,
                 'TA 130g S&P chicken bites ×6. GFF481R. Pedro id 2360.',
                 [(ccspb1, 6, U_UNIT)]),

                ('CCSPB-1R6TEB', 'Booths Snacks - Salt & Pepper Chicken With Sweet Chilli Dip | 180G X 6',
                 180, 6, True,
                 'Booths 180g S&P chicken bites ×6. GFF481R. Sleeve S666-04, box OC002. Pedro id 1250. '
                 'Sweet chilli dip not mapped — please add dip product and update recipe.',
                 [(ccspb1, 6, U_UNIT), (needed['S666-04'], 6, U_UNIT), (needed['OC002'], 1, U_UNIT)]),

                ('FCKATB2-R8TCO', 'Cook - Katsu Chicken Bites | 150G X 8', 150, 8, False,
                 'Cook 150g katsu chicken bites ×8. GFF480R / GFF516R Cook-specific (separate PDF). '
                 'Pedro id 3312. Please verify if GFF480R or GFF516R applies and update recipe.',
                 [(cckatb1, 8, U_UNIT)]),
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
        self.stdout.write(self.style.SUCCESS('chicken bites chains done'))
