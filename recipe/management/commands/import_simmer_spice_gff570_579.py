"""Draft simmer spice SKUs GFF570R-S … GFF579R-S from signed PDFs. Never activates.

  python manage.py import_simmer_spice_gff570_579
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_G, LOC,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_DIR = DOCS / 'simmer' / 'fwrecipesfornewsimmerlines'
CAT_SPICE = 137  # Spice Pack (existing simmer spices)
COOK, MARIN = 82, 85

# code, name, dest, pdf, note, lines [(rm, grams), ...]
SPICES = (
    (
        'GFF570R-S',
        'Chipotle Sauce (Simmers) - 570 - Spices',
        COOK,
        'GFF570R-S - CHIPOTLE SAUCE (SIMMERS)- Spices - v.1.pdf',
        'Draft from the signed spice sheet. Please add garlic granules with the catalogue code, 500 g.',
        (
            ('SPICE0-12', 10000),
            ('SAUCE0-19', 10000),
            ('SAUCE0-35', 7500),
            ('SPICE0-30', 500),
            ('SPICE0-19', 200),
        ),
    ),
    (
        'GFF571R-S',
        'Mexican Rice (Simmers) - 571 - Spices',
        COOK,
        'GFF571R-S - MEXICAN RICE (SIMMERS)- Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF571R-S issue 1.',
        (
            ('SAUCE0-19', 4000),
            ('SPICE0-15', 500),
        ),
    ),
    (
        'GFF573R-S',
        'Mediterranean Sauce (Simmers) - 573 - Spices',
        COOK,
        'GFF573R-S -MEDITERRANEAN SAUCE (SIMMERS)- Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF573R-S issue 1.',
        (
            ('SPICE0-02', 750),
            ('SPICE0-49', 750),
            ('SPICE0-03', 750),
            ('SPICE0-47', 350),
            ('SPICE0-24', 150),
        ),
    ),
    (
        'GFF574R-S',
        'Pesto Marinated Chicken (Simmers) - 574 - Spices',
        MARIN,
        'GFF574R-S -PESTO MARINATED CHICKEN (SIMMERS)- Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF574R-S issue 1.',
        (
            ('SPICE0-02', 600),
            ('SPICE0-35', 150),
        ),
    ),
    (
        'GFF575R-S',
        'Cacciatore Sauce (Simmers) - 575 - Spices',
        COOK,
        'GFF575R-S -CACCIATORE SAUCE (SIMMERS) - Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF575R-S issue 1.',
        (
            ('SPICE0-03', 1600),
            ('SPICE0-47', 800),
            ('SPICE0-02', 500),
            ('SPICE0-49', 500),
            ('SPICE0-24', 200),
        ),
    ),
    (
        'GFF578R-S',
        'Red Thai Curry Sauce (Simmers) - 578 - Spices',
        COOK,
        'GFF578R-S -RED THAI CURRY SAUCE (SIMMERS)- Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF578R-S issue 1.',
        (
            ('SAUCE0-35', 4800),
            ('SAUCE0-22', 3200),
            ('VEGFRO-33', 1600),
            ('VEGFRO-43', 1600),
            ('VEGFRO-38', 400),
            ('SPICE0-15', 400),
            ('SPICE0-02', 160),
        ),
    ),
    (
        'GFF579R-S',
        'Thai Style Rice (Simmers) - 579 - Spices',
        COOK,
        'GFF579R-S -THAI STYLE RICE (SIMMERS) - Spices - v.1.pdf',
        'Draft from the signed spice sheet. Amounts are grams from GFF579R-S issue 1.',
        (
            ('VEGFRO-43', 1800),
            ('VEGFRO-33', 1800),
            ('SPICE0-02', 360),
        ),
    ),
)


class Command(BaseCommand):
    help = 'Create GFF570–579 simmer spice SKUs from signed PDFs. Draft only.'

    def handle(self, *args, **options):
        rm_codes = sorted({c for *_, lines in SPICES for c, _ in lines})
        with transaction.atomic():
            needed = require_products(*rm_codes)
            out = []
            for code, name, dest, pdf_name, note, lines in SPICES:
                row, created = make_product(
                    code, name, CAT_SPICE, U_G, LOC['spice'], dest,
                    None, False, 'Date type: Use By',
                )
                if row.alternate_recipe_code != code:
                    row.alternate_recipe_code = code
                    row.save(update_fields=['alternate_recipe_code', 'updated_at'])
                version = make_draft(row, note)
                clear_lines(version)
                for i, (rm, grams) in enumerate(lines, 1):
                    add_line(version, i, needed[rm], grams, U_G)
                att = attach_file(version, PDF_DIR / pdf_name, f'{code} signed spice sheet')
                sync_has_recipe(row.id)
                out.append(
                    f"{'NEW' if created else 'reuse'} {code} id={row.id} "
                    f"v={version.id} lines={len(lines)} att={att}"
                )
        self.stdout.write('\n'.join(out))
        self.stdout.write(self.style.SUCCESS('simmer spice drafts imported'))
