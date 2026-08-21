"""Draft CCBAR1-R4TEB from signed PDFs + GFF232MC QAS. Never activates.

  python manage.py import_live_ccbar1_r4teb
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

PDF_CHICKEN = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF164R British Marinated Chicken V.4.pdf'
)
QAS = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Booths QAS'
    / 'GFF232MC - BOOTHS - Balti Chicken with Pilau Rice 400g V.3.xlsx'
)

CAT_MIX, CAT_PACKED, CAT_FG = 164, 76, 127

NOTE_CHICKEN = 'Draft from the signed mix sheet. Amounts are grams from GFF164R issue 4.'
NOTE_HR = (
    'Pack from GFF232MC QAS: 165 g pilau, 160 g balti and 75 g marinated chicken. '
    'Outer box code is not in the new catalogue so OC0010 was used on the FG.'
)
NOTE_FG = 'Booths case of four 400 g packs with sleeve and outer box.'


class Command(BaseCommand):
    help = 'Fill CCBAR1-R4TEB tree: GFF164R mix, CCBAR1 pack, FG. Reuses GFF333/GFF143 mixes.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'GFF333R-Mx', 'GFF143R-Mx', 'PROTEIN-31', 'INGRAD-01',
                'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-27', 'SPICE0-02',
                'PKRHP001-10', 'PKKMP001-03', 'S667-12', 'OC0010', 'CCBAR1-R4TEB',
            )

            chicken, c_ch = make_product(
                'GFF164R-Mx', 'British Marinated Chicken - 164 - Mix',
                CAT_MIX, U_G, LOC['marin'], LOC['hr'], 'GFF164R', False, NOTE_CHICKEN,
            )
            packed, c_pk = make_product(
                'CCBAR1', 'Balti Chicken with Pilau Rice',
                CAT_PACKED, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF232MC', True, NOTE_HR,
                product_class=CLASS_READY,
            )
            fg = needed['CCBAR1-R4TEB']
            if fg.remarks != NOTE_FG:
                fg.remarks = NOTE_FG
                fg.save(update_fields=['remarks', 'updated_at'])

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

            v_ch = make_draft(chicken, NOTE_CHICKEN)
            clear_lines(v_ch)
            add_line(v_ch, 1, needed['PROTEIN-31'], 150000, U_G)
            add_line(v_ch, 2, needed['INGRAD-01'], 3000, U_G)
            add_line(v_ch, 3, needed['VEGFRO-15'], 907, U_G)
            add_line(v_ch, 4, needed['VEGFRO-11'], 758, U_G)
            add_line(v_ch, 5, needed['SPICE0-02'], 758, U_G)
            add_line(v_ch, 6, needed['VEGFRO-27'], 379, U_G)

            v_hr = make_draft(packed, NOTE_HR)
            clear_lines(v_hr)
            add_line(v_hr, 1, needed['GFF143R-Mx'], 165, U_G)
            add_line(v_hr, 2, needed['GFF333R-Mx'], 160, U_G)
            add_line(v_hr, 3, chicken, 75, U_G)
            add_line(v_hr, 4, needed['PKRHP001-10'], 1, U_TRAY)
            add_line(v_hr, 5, needed['PKKMP001-03'], '0.2', U_M)

            v_fg = make_draft(fg, NOTE_FG)
            clear_lines(v_fg)
            add_line(v_fg, 1, packed, 4, U_UNIT)
            add_line(v_fg, 2, needed['S667-12'], 4, U_UNIT)
            add_line(v_fg, 3, needed['OC0010'], 1, U_UNIT)

            att = [
                attach_file(v_ch, PDF_CHICKEN, 'GFF164R V.4 mix sheet'),
                attach_file(v_hr, QAS, 'GFF232MC V.3 packed-item QAS'),
                attach_file(v_fg, QAS, 'GFF232MC V.3 names this SKU'),
            ]
            for p in (chicken, packed, fg):
                sync_has_recipe(p.id)

        self.stdout.write('\n'.join([
            f"{'NEW' if c_ch else 'reuse'} GFF164R-Mx id={chicken.id}",
            f"{'NEW' if c_pk else 'reuse'} CCBAR1 id={packed.id}",
            f'reuse CCBAR1-R4TEB id={fg.id}',
            'attachments: ' + ', '.join(att),
        ]))
        self.stdout.write(self.style.SUCCESS('CCBAR1-R4TEB draft imported'))
