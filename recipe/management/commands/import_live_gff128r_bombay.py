"""Draft GFF128R Bombay Potato mix + spice from signed PDFs. Never activates.

  python manage.py import_live_gff128r_bombay
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
    / 'GFF128R - BOMBAY POTATO - V.3.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF128R-S Bombay Potato Booths- Spices, V.3.pdf'
)

CAT_SPICE = 6
CAT_STEAM = 143
CAT_MIX = 164

NOTE_SPICE = 'Draft from the signed spice sheet. Amounts are grams from GFF128R-S issue 3.'
NOTE_STEAM = (
    'No mix sheet for this steam. Please add potato diced 20mm 27200 g on this '
    'recipe with the catalogue code.'
)
NOTE_MIX = 'Draft from the signed mix sheet. Amounts are grams from GFF128R issue 3.'


class Command(BaseCommand):
    help = 'Fill GFF128R-Mx + GFF128R-S from signed PDFs. Draft only.'

    def handle(self, *args, **options):
        with transaction.atomic():
            needed = require_products(
                'AFFINITY-S1',
                'SAUCE0-01', 'SAUCE0-03', 'SAUCE0-07', 'SAUCE0-12', 'SAUCE0-15',
                'INGRAD-01',
                'VEGCHI-09', 'VEGFRO-11', 'VEGFRO-15',
                'SPICE0-02', 'SPICE0-03', 'SPICE0-04', 'SPICE0-05',
                'SPICE0-06', 'SPICE0-08', 'SPICE0-09', 'SPICE0-11',
                'SPICE0-15', 'SPICE0-33',
            )

            spice, c_sp = make_product(
                'GFF128R-S', 'Bombay Potato - 128 - Spices',
                CAT_SPICE, U_G, LOC['spice'], LOC['lwr'], 'GFF128R-S', False, NOTE_SPICE,
            )
            steam, c_st = make_product(
                'GFF128R-St', 'Steamed - Potato - Diced - 20mm - 128 - Steaming',
                CAT_STEAM, U_G, LOC['steam'], LOC['lwr'], None, False, NOTE_STEAM,
            )
            mix, c_mx = make_product(
                'GFF128R-Mx', 'Bombay Potato - 128 - Mix',
                CAT_MIX, U_G, LOC['lwr'], LOC['hr'], 'GFF128R', False, NOTE_MIX,
            )
            if mix.source_container_id != LOC['lwr'] or mix.destination_container_id != LOC['hr']:
                mix.source_container_id = LOC['lwr']
                mix.destination_container_id = LOC['hr']
                mix.save(update_fields=['source_container_id', 'destination_container_id', 'updated_at'])

            v_sp = make_draft(spice, NOTE_SPICE)
            clear_lines(v_sp)
            add_line(v_sp, 1, needed['SAUCE0-12'], 1020, U_G)
            add_line(v_sp, 2, needed['SPICE0-02'], 442, U_G)
            add_line(v_sp, 3, needed['SPICE0-03'], 340, U_G)
            add_line(v_sp, 4, needed['SPICE0-09'], 221, U_G)
            add_line(v_sp, 5, needed['SPICE0-15'], 221, U_G)
            add_line(v_sp, 6, needed['SPICE0-08'], 170, U_G)
            add_line(v_sp, 7, needed['SPICE0-06'], 136, U_G)
            add_line(v_sp, 8, needed['SPICE0-33'], 119, U_G)
            add_line(v_sp, 9, needed['SPICE0-04'], 102, U_G)
            add_line(v_sp, 10, needed['SPICE0-05'], 102, U_G)
            add_line(v_sp, 11, needed['SPICE0-11'], 68, U_G)

            v_st = make_draft(steam, NOTE_STEAM)
            clear_lines(v_st)

            v_mx = make_draft(mix, NOTE_MIX)
            clear_lines(v_mx)
            add_line(v_mx, 1, steam, 27200, U_G)
            add_line(v_mx, 2, needed['SAUCE0-07'], 10200, U_G)
            add_line(v_mx, 3, needed['SAUCE0-01'], 6120, U_G)
            add_line(v_mx, 4, needed['AFFINITY-S1'], 6120, U_G)
            add_line(v_mx, 5, needed['SAUCE0-15'], 4080, U_G)
            add_line(v_mx, 6, needed['SAUCE0-03'], 2040, U_G)
            add_line(v_mx, 7, needed['INGRAD-01'], 1700, U_G)
            add_line(v_mx, 8, needed['VEGCHI-09'], 340, U_G)
            add_line(v_mx, 9, needed['VEGFRO-11'], 221, U_G)
            add_line(v_mx, 10, needed['VEGFRO-15'], 221, U_G)
            add_line(v_mx, 11, spice, 2941, U_G)

            att = [
                attach_file(v_sp, PDF_SPICE, 'GFF128R-S V.3 spice mix sheet'),
                attach_file(v_mx, PDF_MIX, 'GFF128R V.3 mix sheet'),
            ]
            sync_has_recipe(spice.id)
            sync_has_recipe(steam.id)
            sync_has_recipe(mix.id)

        self.stdout.write(
            f"{'NEW' if c_sp else 'reuse'} GFF128R-S id={spice.id}\n"
            f"{'NEW' if c_st else 'reuse'} GFF128R-St id={steam.id}\n"
            f"{'NEW' if c_mx else 'reuse'} GFF128R-Mx id={mix.id}\n"
            f'attachments: {", ".join(att)}\n'
            f'drafts spice={v_sp.id} steam={v_st.id} mix={v_mx.id}'
        )
        self.stdout.write(self.style.SUCCESS('GFF128R draft imported'))
