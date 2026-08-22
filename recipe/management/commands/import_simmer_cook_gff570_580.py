"""Draft simmer cook/steam/marin SKUs GFF570R–GFF580R from signed PDFs. Never activates.

  python manage.py import_simmer_cook_gff570_580
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from recipe.utils import sync_has_recipe

from ._live202_helpers import (
    DOCS, U_G,
    make_product, make_draft, add_line, clear_lines, require_products, attach_file,
)

PDF_DIR = DOCS / 'simmer' / 'fwrecipesfornewsimmerlines'
COOK, HR, STEAM, MARIN, MARIN_DST = 82, 4, 223, 85, 81
CAT_SAUCE, CAT_RICE, CAT_STEAM, CAT_MARIN = 161, 162, 143, 9124

# code, name, cat, src, dst, alt, pdf, note, lines
ITEMS = (
    (
        'GFF570R - C',
        'Chipotle Sauce (Simmers) - 570 - Cooking',
        CAT_SAUCE, COOK, HR, 'GFF570R',
        'GFF570R-  CHIPOTLE SAUCE (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Both water lines are on one water product.',
        (
            ('AFFINITY-S1', 56000),  # water-1 50000 + water-2 6000
            ('VEGCHI-21', 20000),
            ('SAUCE0-02', 10000),
            ('SAUCE0-03', 7500),
            ('VEGFRO-11', 5000),
            ('VEGFRO-06', 5000),
            ('VEGFRO-10', 4000),
            ('INGRAD-38', 3000),
            ('VEGFRO-28', 3000),
            ('INGRAD-17', 3000),
            ('GFF570R-S', 28700),
        ),
    ),
    (
        'GFF571R - C',
        'Mexican Rice (Simmers) - 571 - Cooking',
        CAT_RICE, COOK, HR, 'GFF571R',
        'GFF571R-MEXICAN RICE (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Amounts are grams from GFF571R issue 1.',
        (
            ('AFFINITY-S1', 82000),
            ('INGRAD-07', 41000),
            ('VEGFRO-02', 8000),
            ('INGRAD-38', 3500),
            ('VEGFRO-11', 2500),
            ('VEGFRO-10', 2500),
            ('VEGFRO-28', 1000),
            ('GFF571R-S', 4500),
        ),
    ),
    (
        'GFF572R - St',
        'Steamed Sweet Corn and Red & Green Peppers (Simmers) - 572 - Steaming',
        CAT_STEAM, STEAM, HR, 'GFF572R',
        'GFF572R-Steamed Sweet Corn and Red & Green Peppers (SIMMERS) -V.1.pdf',
        'Draft from the signed steam sheet. Amounts are grams from GFF572R issue 1.',
        (
            ('VEGFRO-31', 50000),
            ('VEGFRO-05', 25000),
            ('VEGFRO-23', 25000),
        ),
    ),
    (
        'GFF573R - C',
        'Mediterranean Sauce (Simmers) - 573 - Cooking',
        CAT_SAUCE, COOK, HR, 'GFF573R',
        'GFF573R- MEDITERRANEAN SAUCE (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Amounts are grams from GFF573R issue 1.',
        (
            ('VEGFRO-32', 30000),
            ('AFFINITY-S1', 20000),
            ('VEGCHI-21', 20000),
            ('VEGFRO-07', 15000),
            ('VEGFRO-02', 8000),
            ('VEGFRO-18', 8000),
            ('SAUCE0-03', 7500),
            ('VEGFRO-40', 7000),
            ('VEGFRO-47', 7000),
            ('VEGFRO-16', 7000),
            ('VEGFRO-55', 3000),
            ('VEGFRO-35', 2500),
            ('INGRAD-38', 2000),
            ('VEGFRO-41', 2000),
            ('GFF573R-S', 2750),
        ),
    ),
    (
        'GFF574R - Ma',
        'Pesto Marinated Chicken (Simmers) - 574 - Marination',
        CAT_MARIN, MARIN, MARIN_DST, 'GFF574R',
        'GFF574R- PESTO MARINATED CHICKEN (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Amounts are grams from GFF574R issue 1.',
        (
            ('PROTEIN-37', 150000),
            ('INGRAD-38', 6000),
            ('SAUCE0-02', 4500),
            ('VEGFRO-41', 3500),
            ('VEGFRO-35', 2500),
            ('INGRAD-23', 1500),
            ('INGRAD-17', 400),
            ('GFF574R-S', 750),
        ),
    ),
    (
        'GFF575R - C',
        'Cacciatore Sauce (Simmers) - 575 - Cooking',
        CAT_SAUCE, COOK, HR, 'GFF575R',
        'GFF575R- CACCIATORE SAUCE (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Please add chopped mushroom frozen 26000 g and sliced black olives 5000 g with catalogue codes.',
        (
            ('AFFINITY-S1', 42000),  # water-1 40000 + water-2 2000
            ('VEGCHI-21', 28000),
            ('SAUCE0-15', 16000),
            ('SAUCE0-03', 10000),
            ('VEGFRO-11', 5000),
            ('INGRAD-38', 3150),
            ('INGRAD-17', 1000),
            ('GFF575R-S', 3600),
        ),
    ),
    (
        'GFF576R - St',
        'Steamed Pasta (Simmers) - 576 - Steaming',
        CAT_STEAM, STEAM, HR, 'GFF576R',
        'GFF576R- STEAMED PASTA (SIMMERS) -V.1.pdf',
        'Draft from the signed steam sheet. Amounts are grams from GFF576R issue 1.',
        (
            ('P-341', 100000),
        ),
    ),
    (
        'GFF577R - St',
        'Steamed Green Beans and Sweet Potato (Simmers) - 577 - Steaming',
        CAT_STEAM, STEAM, HR, 'GFF577R',
        'GFF577R-Steamed Green Beans and Sweet Potato (SIMMERS)  -V.1.pdf',
        'Draft from the signed steam sheet. Please add diced green beans 12mm frozen 50000 g with the catalogue code.',
        (
            ('VEGFRO-07', 50000),
        ),
    ),
    (
        'GFF578R - C',
        'Red Thai Curry Sauce (Simmers) - 578 - Cooking',
        CAT_SAUCE, COOK, HR, 'GFF578R',
        'GFF578R- RED THAI CURRY SAUCE (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Both water lines are on one water product.',
        (
            ('AFFINITY-S1', 43200),  # water-1 40000 + water-2 3200
            ('DAIRY0-01', 40000),
            ('VEGFRO-05', 16000),
            ('SAUCE0-07', 8000),
            ('VEGFRO-06', 4000),
            ('SPICE0-03', 3200),
            ('INGRAD-38', 2400),
            ('SAUCE0-03', 2400),
            ('VEGFRO-15', 1600),
            ('VEGFRO-11', 1600),
            ('VEGFRO-28', 1600),
            ('SAUCE0-25', 1600),
            ('INGRAD-17', 1600),
            ('GFF578R-S', 12160),
        ),
    ),
    (
        'GFF579R - C',
        'Thai Style Rice (Simmers) - 579 - Cooking',
        CAT_RICE, COOK, HR, 'GFF579R',
        'GFF579R- THAI STYLE RICE  (SIMMERS) -V.1.pdf',
        'Draft from the signed mix sheet. Amounts are grams from GFF579R issue 1.',
        (
            ('AFFINITY-S1', 90000),
            ('INGRAD-07', 45000),
            ('VEGFRO-06', 4500),
            ('INGRAD-38', 2700),
            ('VEGFRO-15', 1800),
            ('VEGFRO-11', 1800),
            ('GFF579R-S', 3960),
        ),
    ),
    (
        'GFF580R - St',
        'Steamed Red Pepper and Carrot Barton (Simmers) - 580 - Steaming',
        CAT_STEAM, STEAM, HR, 'GFF580R',
        'GFF580R- Steamed Red Pepper and Carrot Barton (SIMMERS) -V.1.pdf',
        'Draft from the signed steam sheet. Please add carrot barton 6x6x45mm frozen 50000 g with the catalogue code.',
        (
            ('VEGFRO-13', 50000),
        ),
    ),
)


class Command(BaseCommand):
    help = 'Create GFF570–580 simmer cook/steam/marin SKUs from signed PDFs. Draft only.'

    def handle(self, *args, **options):
        rm_codes = sorted({c for *_, lines in ITEMS for c, _ in lines})
        with transaction.atomic():
            needed = require_products(*rm_codes)
            out = []
            for code, name, cat, src, dst, alt, pdf_name, note, lines in ITEMS:
                row, created = make_product(
                    code, name, cat, U_G, src, dst,
                    None, False, 'Date type: Use By',
                )
                if row.alternate_recipe_code != alt:
                    row.alternate_recipe_code = alt
                    row.save(update_fields=['alternate_recipe_code', 'updated_at'])
                version = make_draft(row, note)
                clear_lines(version)
                for i, (rm, grams) in enumerate(lines, 1):
                    add_line(version, i, needed[rm], grams, U_G)
                att = attach_file(version, PDF_DIR / pdf_name, f'{alt} signed sheet')
                sync_has_recipe(row.id)
                pdf_total = sum(g for _, g in lines)
                out.append(
                    f"{'NEW' if created else 'reuse'} {code} id={row.id} "
                    f"v={version.id} lines={len(lines)} qty={pdf_total} att={att}"
                )
        self.stdout.write('\n'.join(out))
        self.stdout.write(self.style.SUCCESS('simmer cook/steam/marin drafts imported'))
