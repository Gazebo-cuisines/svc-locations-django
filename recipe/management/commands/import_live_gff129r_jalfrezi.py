"""Draft GFF129R Jalfrezi mix + spice from signed PDFs. Never activates.

  python manage.py import_live_gff129r_jalfrezi
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
    / 'GFF129R - JALFREZI SAUCE V.3.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF129R-S - Jalfrezi Sauce Booths - Spices V.3.pdf'
)

CAT_SPICE = 6
CAT_MIX = 164

NOTE_SPICE = 'Draft from the signed spice sheet. Amounts are grams from GFF129R-S issue 3.'
NOTE_MIX = 'Draft from the signed mix sheet. Amounts are grams from GFF129R issue 3.'


class Command(BaseCommand):
    help = 'Fill GFF129R-Mx + GFF129R-S from signed PDFs. Draft only.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'AFFINITY-S1', 'INGRAD-01',
                'SAUCE0-03', 'SAUCE0-07', 'SAUCE0-12',
                'VEGCHI-01', 'VEGCHI-09', 'VEGCHI-10', 'VEGCHI-13',
                'VEGFRO-11', 'VEGFRO-15', 'VEGFRO-27', 'VEGFRO-28',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-04', 'SPICE0-05',
                'SPICE0-08', 'SPICE0-09', 'SPICE0-11', 'SPICE0-15',
            )

            spice, c_sp = make_product(
                'GFF129R-S', 'Jalfrezi Sauce - 129 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF129R-S', False, NOTE_SPICE,
            )
            mix, c_mx = make_product(
                'GFF129R-Mx', 'Jalfrezi Sauce - 129 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF129R', False, NOTE_MIX,
            )
            if mix.source_container_id != LOC['lwr'] or mix.destination_container_id != LOC['hr']:
                mix.source_container_id = LOC['lwr']
                mix.destination_container_id = LOC['hr']
                mix.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])

            v_sp = make_draft(spice, NOTE_SPICE)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SPICE0-04'], 80, U_G)
            add_line(v_sp, 2, needed['SPICE0-08'], 352, U_G)
            add_line(v_sp, 3, needed['SPICE0-09'], 208, U_G)
            add_line(v_sp, 4, needed['SPICE0-05'], 144, U_G)
            add_line(v_sp, 5, needed['SPICE0-15'], 352, U_G)
            add_line(v_sp, 6, needed['SPICE0-02'], 416, U_G)
            add_line(v_sp, 7, needed['SPICE0-11'], 64, U_G)
            add_line(v_sp, 8, needed['SAUCE0-12'], 2112, U_G)
            add_line(v_sp, 9, needed['SPICE0-03'], 1408, U_G)

            v_mx = make_draft(mix, NOTE_MIX)
            clear_lines(v_mx)
            add_line(v_mx, 1, needed['INGRAD-01'], 4224, U_G)
            add_line(v_mx, 2, needed['VEGCHI-01'], 4224, U_G)
            add_line(v_mx, 3, needed['VEGFRO-11'], 1200, U_G)
            add_line(v_mx, 4, needed['VEGFRO-27'], 416, U_G)
            add_line(v_mx, 5, needed['VEGFRO-28'], 288, U_G)
            add_line(v_mx, 6, needed['VEGFRO-15'], 1696, U_G)
            add_line(v_mx, 7, needed['SAUCE0-07'], 15488, U_G)
            add_line(v_mx, 8, needed['SAUCE0-03'], 10976, U_G)
            add_line(v_mx, 9, needed['AFFINITY-S1'], 17600, U_G)
            add_line(v_mx, 10, needed['VEGCHI-13'], 11264, U_G)
            add_line(v_mx, 11, needed['VEGCHI-10'], 9152, U_G)
            add_line(v_mx, 12, needed['VEGCHI-09'], 848, U_G)
            add_line(v_mx, 13, spice, 5136, U_G)

            att = [
                attach_file(v_sp, PDF_SPICE, 'GFF129R-S V.3 spice mix sheet'),
                attach_file(v_mx, PDF_MIX, 'GFF129R V.3 mix sheet'),
            ]
            sync_has_recipe(spice.id)
            sync_has_recipe(mix.id)

        self.stdout.write(
            f"{'NEW' if c_sp else 'reuse'} GFF129R-S id={spice.id}\n"
            f"{'NEW' if c_mx else 'reuse'} GFF129R-Mx id={mix.id}\n"
            f'attachments: {", ".join(att)}\n'
            f'drafts spice={v_sp.id} mix={v_mx.id}'
        )
        self.stdout.write(self.style.SUCCESS('GFF129R draft imported'))
