"""Draft CCBAR1-R4TCC from signed PDFs + GFF394MC QAS. Never activates.

  python manage.py import_live_ccbar1_r4tcc
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

PDF_BALTI = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF333R - Balti Sauce V.2.pdf'
)
PDF_BALTI_S = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF333R-S - Balti Sauce - Spices V.2.pdf'
)
PDF_PILAU_S = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF143R-S Pilau Rice - Spices V.4.pdf'
)
QAS = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'GAZEBO CUISINE'
    / 'GFF394MC - Chicken Balti with Pilau Rice 400g V.2.xlsx'
)

CAT_SPICE, CAT_MIX, CAT_PACKED, CAT_FG = 6, 164, 76, 127

NOTE_BALTI_S = 'Draft from the signed spice sheet. Amounts are grams from GFF333R-S issue 2.'
NOTE_BALTI = 'Draft from the signed mix sheet. Amounts are grams from GFF333R issue 2.'
NOTE_PILAU_S = 'Draft from the signed spice sheet. Amounts are grams from GFF143R-S issue 4.'
NOTE_PILAU = (
    'No signed pilau mix sheet in Harvi. Batch grams from Pedro tblproducttree for GFF143R cook.'
)
NOTE_HR = (
    'Pack from GFF394MC QAS: 180 g pilau, 160 g balti and 60 g cooked chicken. '
    'Outer box code is not in the new catalogue so OC0010 was used on the FG.'
)
NOTE_FG = 'Gazebo case of four 400 g packs with sleeve and outer box.'


class Command(BaseCommand):
    help = 'Fill CCBAR1-R4TCC tree: GFF333R + GFF143R mixes, GCBAR1 pack, FG. Draft only.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'AFFINITY-S1', 'INGRAD-01', 'INGRAD-07', 'SAUCE0-01', 'SAUCE0-02',
                'SAUCE0-03', 'SAUCE0-07', 'SAUCE0-15', 'SAUCE0-30',
                'DAIRY0-02', 'DAIRY0-04', 'VEGCHI-09', 'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-27',
                'PROTEIN-18', 'PKRHP001-10', 'PKKMP001-03', 'S667-13', 'OC0010',
                'CCBAR1-R4TCC',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-04', 'SPICE0-05', 'SPICE0-08',
                'SPICE0-09', 'SPICE0-11', 'SPICE0-13', 'SPICE0-15', 'SPICE0-27',
                'SPICE0-28', 'SPICE0-31', 'SPICE0-32', 'SPICE0-33', 'SPICE0-35',
                'SPICE0-38', 'SPICE0-39', 'SPICE0-45', 'SPICE0-55',
            )

            balti_s, c_bs = make_product(
                'GFF333R-S', 'Balti Sauce - 333 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF333R-S', False, NOTE_BALTI_S,
            )
            balti, c_bl = make_product(
                'GFF333R-Mx', 'Balti Sauce - 333 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF333R', False, NOTE_BALTI,
            )
            pilau_s, c_ps = make_product(
                'GFF143R-S', 'Pilau Rice - 143 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF143R-S', False, NOTE_PILAU_S,
            )
            pilau, c_pl = make_product(
                'GFF143R-Mx', 'Pilau Rice - 143 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF143R', False, NOTE_PILAU,
            )
            packed, c_pk = make_product(
                'GCBAR1', 'GC - Balti Chicken with Pilau Rice',
                CAT_PACKED, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF394MC', True, NOTE_HR,
                product_class=CLASS_READY,
            )
            fg = needed['CCBAR1-R4TCC']
            if fg.remarks != NOTE_FG:
                fg.remarks = NOTE_FG
                fg.save(update_fields=['remarks', 'updated_at'])

            for mix in (balti, pilau):
                if mix.source_container_id != LOC['lwr'] or mix.destination_container_id != LOC['hr']:
                    mix.source_container_id = LOC['lwr']
                    mix.destination_container_id = LOC['hr']
                    mix.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])

            ProductPackaging.objects.update_or_create(
                product_id=packed.id, defaults={'pack_weight': Decimal('400')},
            )
            ProductPackaging.objects.update_or_create(
                product_id=fg.id,
                defaults={
                    'items_per_unit': Decimal('4'),
                    'unitary_weight': Decimal('400'),
                    'pack_weight': Decimal('1600'),
                },
            )
            ProductShelfLife.objects.get_or_create(product_id=fg.id, defaults={'shelf_life_days': 14})
            flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
            if not flags.is_sales_item:
                flags.is_sales_item = True
                flags.has_plan = True
                flags.save(update_fields=['is_sales_item', 'has_plan'])

            v_bs = make_draft(balti_s, NOTE_BALTI_S)
            clear_lines(v_bs)
            add_line(v_bs, 1, needed['SPICE0-33'], 300, U_G)
            add_line(v_bs, 2, needed['SPICE0-04'], 300, U_G)
            add_line(v_bs, 3, needed['SPICE0-38'], 200, U_G)
            add_line(v_bs, 4, needed['SPICE0-13'], 100, U_G)
            add_line(v_bs, 5, needed['SPICE0-03'], 2000, U_G)
            add_line(v_bs, 6, needed['SPICE0-02'], 900, U_G)
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

            v_bl = make_draft(balti, NOTE_BALTI)
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
            add_line(v_bl, 13, balti_s, 6350, U_G)

            v_ps = make_draft(pilau_s, NOTE_PILAU_S)
            clear_lines(v_ps)
            add_line(v_ps, 1, needed['SPICE0-02'], 760, U_G)
            add_line(v_ps, 2, needed['SPICE0-04'], 280, U_G)
            add_line(v_ps, 3, needed['SPICE0-31'], 100, U_G)
            add_line(v_ps, 4, needed['SPICE0-39'], 40, U_G)
            add_line(v_ps, 5, needed['SPICE0-11'], 280, U_G)
            add_line(v_ps, 6, needed['SPICE0-27'], 40, U_G)

            v_pl = make_draft(pilau, NOTE_PILAU)
            clear_lines(v_pl)
            add_line(v_pl, 1, needed['AFFINITY-S1'], 87888, U_G)
            add_line(v_pl, 2, needed['INGRAD-07'], 43028.5, U_G)
            add_line(v_pl, 3, needed['SAUCE0-15'], 3295.8, U_G)
            add_line(v_pl, 4, needed['INGRAD-01'], 2197.2, U_G)
            add_line(v_pl, 5, pilau_s, 1500, U_G)

            v_hr = make_draft(packed, NOTE_HR)
            clear_lines(v_hr)
            add_line(v_hr, 1, pilau, 180, U_G)
            add_line(v_hr, 2, balti, 160, U_G)
            add_line(v_hr, 3, needed['PROTEIN-18'], 60, U_G)
            add_line(v_hr, 4, needed['PKRHP001-10'], 1, U_TRAY)
            add_line(v_hr, 5, needed['PKKMP001-03'], '0.2', U_M)

            v_fg = make_draft(fg, NOTE_FG)
            clear_lines(v_fg)
            add_line(v_fg, 1, packed, 4, U_UNIT)
            add_line(v_fg, 2, needed['S667-13'], 4, U_UNIT)
            add_line(v_fg, 3, needed['OC0010'], 1, U_UNIT)

            att = [
                attach_file(v_bs, PDF_BALTI_S, 'GFF333R-S V.2 spice mix sheet'),
                attach_file(v_bl, PDF_BALTI, 'GFF333R V.2 mix sheet'),
                attach_file(v_ps, PDF_PILAU_S, 'GFF143R-S V.4 spice mix sheet'),
                attach_file(v_hr, QAS, 'GFF394MC V.2 packed-item QAS'),
                attach_file(v_fg, QAS, 'GFF394MC V.2 names this SKU'),
            ]
            for p in (balti_s, balti, pilau_s, pilau, packed, fg):
                sync_has_recipe(p.id)

        self.stdout.write('\n'.join([
            f"{'NEW' if c_bs else 'reuse'} GFF333R-S id={balti_s.id}",
            f"{'NEW' if c_bl else 'reuse'} GFF333R-Mx id={balti.id}",
            f"{'NEW' if c_ps else 'reuse'} GFF143R-S id={pilau_s.id}",
            f"{'NEW' if c_pl else 'reuse'} GFF143R-Mx id={pilau.id}",
            f"{'NEW' if c_pk else 'reuse'} GCBAR1 id={packed.id}",
            f'reuse CCBAR1-R4TCC id={fg.id}',
            'attachments: ' + ', '.join(att),
        ]))
        self.stdout.write(self.style.SUCCESS('CCBAR1-R4TCC draft imported'))
