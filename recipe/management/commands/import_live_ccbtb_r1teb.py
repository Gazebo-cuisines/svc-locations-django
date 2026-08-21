"""Draft CCBTB-R1TEB from signed GFF503R PDFs + GFF469MC QAS. Never activates.

  python manage.py import_live_ccbtb_r1teb
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import ProductFlags, ProductPackaging, ProductShelfLife
from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_UNIT, U_G, U_TRAY, LOC, CLASS_SNACK,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MIX = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF503R-Xmas Breaded Chicken Bites-v.2.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF503R-S-Xmas Breaded Chicken Bites -Spices v.1.pdf'
)
QAS = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Booths Xmas'
    / 'GFF469MC -BOOTHS-XMAS  - 8 Breaded Chicken Bites with BBQ Dip 240g v.2.xlsx'
)

CAT_SPICE, CAT_MIX, CAT_PACKED, CAT_FG = 6, 164, 76, 127

NOTE_SPICE = 'Draft from the signed spice sheet. Amounts are grams from GFF503R-S issue 1.'
NOTE_MIX = 'Draft from the signed mix sheet. Amounts are grams from GFF503R issue 2.'
NOTE_HR = (
    'Pack from GFF469MC QAS: 200 g breaded bites and 40 g barbecue sauce. '
    'The Booths sleeve and outer box are not in the new catalogue.'
)
NOTE_FG = 'Booths retail pack of eight bites with dip. Sleeve omitted pending catalogue code.'


class Command(BaseCommand):
    help = 'Fill CCBTB-R1TEB tree: GFF503R mix/spice, CCBTB pack, FG. Draft only.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'PROTEIN-36', 'AFFINITY-S1', 'SPICE0-59', 'INGRAD-76', 'INGRAD-20',
                'SPICE0-02', 'SPICE0-22', 'SPICE0-19', 'SAUCE0-38',
                'PKESS001-07', 'PKKMP001-07', 'PKJEN001-03', 'OC002', 'CCBTB-R1TEB',
            )

            spice, c_sp = make_product(
                'GFF503R-S', 'Xmas Breaded Chicken Bites - 503 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF503R-S', False, NOTE_SPICE,
            )
            mix, c_mx = make_product(
                'GFF503R-Mx', 'Xmas Breaded Chicken Bites - 503 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['fry'], 'GFF503R', False, NOTE_MIX,
            )
            packed, c_pk = make_product(
                'CCBTB', 'Booths 8 Breaded Chicken Bites W/BBQ Dip',
                CAT_PACKED, U_UNIT, LOC['fry'], LOC['sleeve'], 'GFF469MC', True, NOTE_HR,
                product_class=CLASS_SNACK,
            )
            fg = needed['CCBTB-R1TEB']
            if fg.remarks != NOTE_FG:
                fg.remarks = NOTE_FG
                fg.save(update_fields=['remarks', 'updated_at'])

            ProductPackaging.objects.update_or_create(
                product_id=packed.id, defaults={'pack_weight': Decimal('240')},
            )
            ProductPackaging.objects.update_or_create(
                product_id=fg.id, defaults={'pack_weight': Decimal('240')},
            )
            ProductShelfLife.objects.get_or_create(product_id=fg.id, defaults={'shelf_life_days': 14})
            flags, _ = ProductFlags.objects.get_or_create(product_id=fg.id)
            if not flags.is_sales_item:
                flags.is_sales_item = True
                flags.has_plan = True
                flags.save(update_fields=['is_sales_item', 'has_plan'])

            v_sp = make_draft(spice, NOTE_SPICE)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['INGRAD-76'], 45000, U_G)
            add_line(v_sp, 2, needed['INGRAD-20'], 6000, U_G)
            add_line(v_sp, 3, needed['SPICE0-02'], 700, U_G)
            add_line(v_sp, 4, needed['SPICE0-22'], 600, U_G)
            add_line(v_sp, 5, needed['SPICE0-19'], 300, U_G)

            v_mx = make_draft(mix, NOTE_MIX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['PROTEIN-36'], 150000, U_G)
            add_line(v_mx, 2, needed['AFFINITY-S1'], 27000, U_G)
            add_line(v_mx, 3, needed['SPICE0-59'], 12000, U_G)
            add_line(v_mx, 4, spice, 53350, U_G)

            v_hr = make_draft(packed, NOTE_HR)
            clear_lines(v_hr)
            add_line(v_hr, 1, mix, 200, U_G)
            add_line(v_hr, 2, needed['SAUCE0-38'], 40, U_G)
            add_line(v_hr, 3, needed['PKESS001-07'], 1, U_TRAY)
            add_line(v_hr, 4, needed['PKKMP001-07'], 1, U_UNIT)
            add_line(v_hr, 5, needed['PKJEN001-03'], 1, U_UNIT)

            v_fg = make_draft(fg, NOTE_FG)
            clear_lines(v_fg)
            add_line(v_fg, 1, packed, 1, U_UNIT)
            add_line(v_fg, 2, needed['OC002'], 1, U_UNIT)

            att = [
                attach_file(v_sp, PDF_SPICE, 'GFF503R-S V.1 spice mix sheet'),
                attach_file(v_mx, PDF_MIX, 'GFF503R V.2 mix sheet'),
                attach_file(v_hr, QAS, 'GFF469MC V.2 packed-item QAS'),
                attach_file(v_fg, QAS, 'GFF469MC V.2 names this SKU'),
            ]
            for p in (spice, mix, packed, fg):
                sync_has_recipe(p.id)

        self.stdout.write('\n'.join([
            f"{'NEW' if c_sp else 'reuse'} GFF503R-S id={spice.id}",
            f"{'NEW' if c_mx else 'reuse'} GFF503R-Mx id={mix.id}",
            f"{'NEW' if c_pk else 'reuse'} CCBTB id={packed.id}",
            f'reuse CCBTB-R1TEB id={fg.id}',
            'attachments: ' + ', '.join(att),
        ]))
        self.stdout.write(self.style.SUCCESS('CCBTB-R1TEB draft imported'))
