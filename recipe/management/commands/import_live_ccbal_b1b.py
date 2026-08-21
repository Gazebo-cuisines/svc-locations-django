"""Draft CCBAL-B1B from signed PDFs + GFF456MC QAS. Never activates.

  python manage.py import_live_ccbal_b1b
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, U_M, U_TRAY, LOC, CLASS_READY,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_Balti = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF554R - Low Salt Balti Sauce V.1.pdf'
)
PDF_Balti_S = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF554R-S - Low Salt Balti Sauce - Spices V.1.pdf'
)
PDF_Tikka = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF142R Chicken Tikka v6.pdf'
)
PDF_Tikka_S = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF142R-S Chicken Tikka  - Spices V.4.pdf'
)
QAS = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Gazebo-Deli'
    / 'GFF456MC-Gazebo  Deli-Balti Chicken 1300g v4.xlsx'
)

CAT_SPICE, CAT_MIX, CAT_PACKED, CAT_FG = 6, 164, 76, 127

NOTE_Balti_S = 'Draft from the signed spice sheet. Amounts are grams from GFF554R-S issue 1.'
NOTE_Balti = 'Draft from the signed mix sheet. Amounts are grams from GFF554R issue 1.'
NOTE_Tikka_S = 'Draft from the signed spice sheet. Amounts are grams from GFF142R-S issue 4.'
NOTE_Tikka = 'Draft from the signed mix sheet. Amounts are grams from GFF142R issue 6.'
NOTE_HR = (
    'Pack from GFF456MC QAS: 430 g chicken tikka and 870 g low-salt balti. '
    'The thermal label is not in the new catalogue, so it was left off.'
)
NOTE_FG = 'Gazebo Deli case of one. Label is not in the new catalogue.'


class Command(BaseCommand):
    help = 'Create CCBAL-B1B draft from GFF554R + GFF142R PDFs and GFF456MC QAS. Never activates.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'AFFINITY-S1', 'INGRAD-01',
                'SAUCE0-01', 'SAUCE0-02', 'SAUCE0-03', 'SAUCE0-07', 'SAUCE0-30',
                'DAIRY0-02', 'DAIRY0-04',
                'VEGCHI-09', 'VEGFRO-10', 'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-27',
                'PROTEIN-31',
                'SPICE0-01', 'SPICE0-02', 'SPICE0-03', 'SPICE0-04', 'SPICE0-05',
                'SPICE0-06', 'SPICE0-07', 'SPICE0-08', 'SPICE0-09', 'SPICE0-11',
                'SPICE0-13', 'SPICE0-15', 'SPICE0-27', 'SPICE0-28', 'SPICE0-32',
                'SPICE0-33', 'SPICE0-35', 'SPICE0-38', 'SPICE0-45', 'SPICE0-55',
                'PKPRO001-20', 'PKPRO001-17',
            )

            tikka_s, c1 = make_product(
                'GFF142R-S', 'Chicken Tikka - 142 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['marin'], 'GFF142R-S', False, NOTE_Tikka_S,
            )
            tikka, c2 = make_product(
                'GFF142R-Mx', 'Chicken Tikka - 142 - Marination',
                CAT_MIX, U_G, LOC['marin'], LOC['hr'], 'GFF142R', False, NOTE_Tikka,
            )
            balti_s, c3 = make_product(
                'GFF554R-S', 'Low Salt Balti Sauce - 554 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF554R-S', False, NOTE_Balti_S,
            )
            balti, c4 = make_product(
                'GFF554R-Mx', 'Low Salt Balti Sauce - 554 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF554R', False, NOTE_Balti,
            )
            hr, c5 = make_product(
                'CCBAL', 'Gazebo Deli - Balti Chicken 1300g',
                CAT_PACKED, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF456MC', True, NOTE_HR,
                product_class=CLASS_READY,
            )
            fg, c6 = make_product(
                'CCBAL-B1B', 'Gz Deli - Balti Chicken | 1250G x 1',
                CAT_FG, U_UNIT, LOC['sleeve'], LOC['dispatch'], None, True, NOTE_FG,
                product_class=CLASS_READY,
            )
            if tikka.source_container_id != LOC['marin'] or tikka.destination_container_id != LOC['hr']:
                tikka.source_container_id = LOC['marin']
                tikka.destination_container_id = LOC['hr']
                tikka.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])
            if balti.source_container_id != LOC['lwr'] or balti.destination_container_id != LOC['hr']:
                balti.source_container_id = LOC['lwr']
                balti.destination_container_id = LOC['hr']
                balti.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])

            ProductPackaging.objects.update_or_create(
                product_id=hr.id, defaults={'pack_weight': Decimal('1300')},
            )
            ProductPackaging.objects.update_or_create(
                product_id=fg.id,
                defaults={
                    'items_per_unit': Decimal('1'),
                    'unitary_weight': Decimal('1300'),
                    'pack_weight': Decimal('1300'),
                },
            )
            ProductShelfLife.objects.get_or_create(
                product_id=fg.id, defaults={'shelf_life_days': 14},
            )
            flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
            if not flags.is_sales_item:
                flags.is_sales_item = True
                flags.has_plan = True
                flags.save(update_fields=['is_sales_item', 'has_plan'])

            v_ts = make_draft(tikka_s, NOTE_Tikka_S)
            clear_lines(v_ts)
            add_line(v_ts, 1, needed['SPICE0-01'], 4000, U_G)
            add_line(v_ts, 2, needed['SPICE0-06'], 300, U_G)
            add_line(v_ts, 3, needed['SPICE0-02'], 600, U_G)
            add_line(v_ts, 4, needed['SAUCE0-30'], 150, U_G)

            v_tk = make_draft(tikka, NOTE_Tikka)
            clear_lines(v_tk)
            add_line(v_tk, 1, needed['PROTEIN-31'], 100000, U_G)
            add_line(v_tk, 2, needed['DAIRY0-02'], 8000, U_G)
            add_line(v_tk, 3, needed['DAIRY0-04'], 4000, U_G)
            add_line(v_tk, 4, needed['SPICE0-07'], 1200, U_G)
            add_line(v_tk, 5, needed['VEGFRO-11'], 1200, U_G)
            add_line(v_tk, 6, needed['VEGFRO-15'], 1200, U_G)
            add_line(v_tk, 7, needed['SAUCE0-02'], 2000, U_G)
            add_line(v_tk, 8, needed['INGRAD-01'], 700, U_G)
            add_line(v_tk, 9, needed['VEGFRO-10'], 700, U_G)
            add_line(v_tk, 10, tikka_s, 5050, U_G)

            v_bs = make_draft(balti_s, NOTE_Balti_S)
            clear_lines(v_bs)
            add_line(v_bs, 1, needed['SPICE0-33'], 300, U_G)
            add_line(v_bs, 2, needed['SPICE0-04'], 300, U_G)
            add_line(v_bs, 3, needed['SPICE0-38'], 200, U_G)
            add_line(v_bs, 4, needed['SPICE0-13'], 100, U_G)
            add_line(v_bs, 5, needed['SPICE0-03'], 2000, U_G)
            add_line(v_bs, 6, needed['SPICE0-02'], 550, U_G)
            add_line(v_bs, 7, needed['SPICE0-05'], 500, U_G)
            add_line(v_bs, 8, needed['SPICE0-15'], 500, U_G)
            add_line(v_bs, 9, needed['SAUCE0-30'], 300, U_G)
            add_line(v_bs, 10, needed['SPICE0-08'], 200, U_G)
            add_line(v_bs, 11, needed['SPICE0-11'], 200, U_G)
            add_line(v_bs, 12, needed['SPICE0-09'], 200, U_G)
            add_line(v_bs, 13, needed['SPICE0-28'], 200, U_G)
            add_line(v_bs, 14, needed['SPICE0-27'], 100, U_G)
            add_line(v_bs, 15, needed['SPICE0-32'], 100, U_G)
            add_line(v_bs, 16, needed['SPICE0-55'], 100, U_G)
            add_line(v_bs, 17, needed['SPICE0-35'], 100, U_G)
            add_line(v_bs, 18, needed['SPICE0-45'], 50, U_G)

            v_bl = make_draft(balti, NOTE_Balti)
            clear_lines(v_bl)
            add_line(v_bl, 1, needed['SAUCE0-01'], 50000, U_G)
            add_line(v_bl, 2, needed['SAUCE0-07'], 40000, U_G)
            add_line(v_bl, 3, needed['AFFINITY-S1'], 20000, U_G)
            add_line(v_bl, 4, needed['DAIRY0-02'], 5000, U_G)
            add_line(v_bl, 5, needed['SAUCE0-03'], 4000, U_G)
            add_line(v_bl, 6, needed['INGRAD-01'], 3000, U_G)
            add_line(v_bl, 7, needed['DAIRY0-04'], 3000, U_G)
            add_line(v_bl, 8, needed['VEGFRO-11'], 2000, U_G)
            add_line(v_bl, 9, needed['VEGCHI-09'], 2000, U_G)
            add_line(v_bl, 10, needed['VEGFRO-15'], 1500, U_G)
            add_line(v_bl, 11, needed['SAUCE0-02'], 1000, U_G)
            add_line(v_bl, 12, needed['VEGFRO-27'], 500, U_G)
            add_line(v_bl, 13, balti_s, 6000, U_G)

            v_hr = make_draft(hr, NOTE_HR)
            clear_lines(v_hr)
            add_line(v_hr, 1, tikka, 430, U_G)
            add_line(v_hr, 2, balti, 870, U_G)
            add_line(v_hr, 3, needed['PKPRO001-20'], 1, U_TRAY)
            add_line(v_hr, 4, needed['PKPRO001-17'], '0.5', U_M)

            v_fg = make_draft(fg, NOTE_FG)
            clear_lines(v_fg)
            add_line(v_fg, 1, hr, 1, U_UNIT)

            att = [
                attach_file(v_ts, PDF_Tikka_S, 'GFF142R-S V.4 spice mix sheet'),
                attach_file(v_tk, PDF_Tikka, 'GFF142R V.6 mix sheet'),
                attach_file(v_bs, PDF_Balti_S, 'GFF554R-S V.1 spice mix sheet'),
                attach_file(v_bl, PDF_Balti, 'GFF554R V.1 mix sheet'),
                attach_file(v_hr, QAS, 'GFF456MC V.4 packed-item QAS'),
                attach_file(v_fg, QAS, 'GFF456MC V.4 names this SKU'),
            ]
            for p in (tikka_s, tikka, balti_s, balti, hr, fg):
                sync_has_recipe(p.id)

        self.stdout.write('\n'.join([
            f"{'NEW' if c1 else 'reuse'} GFF142R-S id={tikka_s.id}",
            f"{'NEW' if c2 else 'reuse'} GFF142R-Mx id={tikka.id}",
            f"{'NEW' if c3 else 'reuse'} GFF554R-S id={balti_s.id}",
            f"{'NEW' if c4 else 'reuse'} GFF554R-Mx id={balti.id}",
            f"{'NEW' if c5 else 'reuse'} CCBAL id={hr.id}",
            f"{'NEW' if c6 else 'reuse'} CCBAL-B1B id={fg.id}",
            'attachments: ' + ', '.join(att),
        ]))
        self.stdout.write(self.style.SUCCESS('CCBAL-B1B draft imported'))
