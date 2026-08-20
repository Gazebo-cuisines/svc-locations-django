"""Draft GFF127R Madras mix + spice from signed PDFs. Never activates.

  python manage.py import_live_gff127r_madras
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_MIX = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF127R - MADRAS SAUCE V.3.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF127R-S - Madras Sauce Booths - Spices V.3.pdf'
)

CAT_SPICE = 6
CAT_MIX = 164

NOTE_SPICE = 'Draft from the signed spice sheet. Amounts are grams from GFF127R-S issue 3.'
NOTE_MIX = (
    'Draft from the signed mix sheet. Amounts are grams from GFF127R issue 3. '
    'Both water lines from the sheet are on one water product.'
)


class Command(BaseCommand):
    help = 'Fill GFF127R-Mx + GFF127R-S from signed PDFs. Draft only.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'AFFINITY-S1',
                'SAUCE0-01', 'SAUCE0-03', 'SAUCE0-07', 'SAUCE0-12',
                'DAIRY0-01', 'INGRAD-01', 'INGRAD-17',
                'VEGCHI-01', 'VEGCHI-10',
                'VEGFRO-10', 'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-27',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-05', 'SPICE0-06',
                'SPICE0-08', 'SPICE0-09', 'SPICE0-11', 'SPICE0-15',
                'SPICE0-23', 'SPICE0-66',
            )

            spice, c_sp = make_product(
                'GFF127R-S', 'Madras Sauce - 127 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF127R-S', False, NOTE_SPICE,
            )
            mix, c_mx = make_product(
                'GFF127R-Mx', 'Madras Sauce - 127 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF127R', False, NOTE_MIX,
            )
            if mix.source_container_id != LOC['lwr'] or mix.destination_container_id != LOC['hr']:
                mix.source_container_id = LOC['lwr']
                mix.destination_container_id = LOC['hr']
                mix.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])

            v_sp = make_draft(spice, NOTE_SPICE)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SPICE0-66'], 720, U_G)
            add_line(v_sp, 2, needed['SPICE0-08'], 540, U_G)
            add_line(v_sp, 3, needed['SPICE0-02'], 540, U_G)
            add_line(v_sp, 4, needed['SPICE0-23'], 360, U_G)
            add_line(v_sp, 5, needed['SAUCE0-12'], 360, U_G)
            add_line(v_sp, 6, needed['SPICE0-09'], 360, U_G)
            add_line(v_sp, 7, needed['SPICE0-03'], 360, U_G)
            add_line(v_sp, 8, needed['SPICE0-15'], 300, U_G)
            add_line(v_sp, 9, needed['SPICE0-05'], 180, U_G)
            add_line(v_sp, 10, needed['SPICE0-11'], 150, U_G)
            add_line(v_sp, 11, needed['SPICE0-06'], 60, U_G)

            v_mx = make_draft(mix, NOTE_MIX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['AFFINITY-S1'], 22140, U_G)
            add_line(v_mx, 2, needed['SAUCE0-07'], 21600, U_G)
            add_line(v_mx, 3, needed['SAUCE0-01'], 20160, U_G)
            add_line(v_mx, 4, needed['DAIRY0-01'], 4680, U_G)
            add_line(v_mx, 5, needed['VEGCHI-10'], 7200, U_G)
            add_line(v_mx, 6, needed['VEGCHI-01'], 3960, U_G)
            add_line(v_mx, 7, needed['INGRAD-01'], 2880, U_G)
            add_line(v_mx, 8, needed['SAUCE0-03'], 2160, U_G)
            add_line(v_mx, 9, needed['VEGFRO-11'], 900, U_G)
            add_line(v_mx, 10, needed['VEGFRO-15'], 840, U_G)
            add_line(v_mx, 11, needed['VEGFRO-27'], 840, U_G)
            add_line(v_mx, 12, needed['VEGFRO-10'], 540, U_G)
            add_line(v_mx, 13, needed['INGRAD-17'], 360, U_G)
            add_line(v_mx, 14, spice, 3930, U_G)

            att = [
                attach_file(v_sp, PDF_SPICE, 'GFF127R-S V.3 spice mix sheet'),
                attach_file(v_mx, PDF_MIX, 'GFF127R V.3 mix sheet'),
            ]
            sync_has_recipe(spice.id)
            sync_has_recipe(mix.id)

        self.stdout.write(
            f"{'NEW' if c_sp else 'reuse'} GFF127R-S id={spice.id}\n"
            f"{'NEW' if c_mx else 'reuse'} GFF127R-Mx id={mix.id}\n"
            f'attachments: {", ".join(att)}\n'
            f'drafts spice={v_sp.id} mix={v_mx.id}'
        )
        self.stdout.write(self.style.SUCCESS('GFF127R draft imported'))
