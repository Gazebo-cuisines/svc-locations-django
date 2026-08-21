"""Draft-only CVSAL-1G6T tree. PDF grams. Does not activate.

  python manage.py import_pilot_cvsal_1g6t
"""

from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import (
    Product,
    ProductFlags,
    ProductPackaging,
    ProductShelfLife,
    ProductTechnical,
)
from recipe.attachments import upload_attachment
from recipe.models import (
    RecipeAttachmentKind,
    RecipeComponent,
    RecipeVersionStatus,
)
from recipe.utils import get_or_create_recipe_with_draft, sync_has_recipe

DOCS = Path(__file__).resolve().parents[3] / 'docs' / 'Recipe-import-task'
PDF_MIXER = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF003R VEGETABLE SAMOSA V.17.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF003R-S VEGETABLE SAMOSA - SPICES V.14.pdf'
)
PDF_STEAM_P = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF241R - Steamed Potato 10mm Diced V.2.pdf'
)
PDF_STEAM_C = (
    DOCS / 'harvi' / '1 LR' / 'Signed copy-Print from here'
    / 'GFF242R - Steamed Carrot 10mm Diced V.3.pdf'
)
QAS_HR = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Gazebo' / 'Grab & Go-QAS'
    / 'GFF186MC - G&G  Vegetable Samosa 100g V.6.xlsx'
)

NOTE_PDF = (
    'Source: signed GFF PDF grams (issue 17 mixer / issue 14 spice). '
    'Pedro 1.25x batch not applied. Draft pending human approval. '
    'Anomalies: docs/Recipe-import-task/anomaly-CVSAL-1G6T.md'
)
NOTE_STEAM_P = (
    'Source: signed GFF241R V.2 (17.12.2018, Magda Shah). '
    'Amount per Mix 10600 g Potato Diced 10mm (Fresh) on Qty. Net/gross left empty. '
    'Evidence: GFF241R - Steamed Potato 10mm Diced V.2.pdf. Draft pending human approval.'
)
NOTE_STEAM_C = (
    'Source: signed GFF242R V.3 (17.12.2018 / frozen carrot added, Magda Shah). '
    'Amount per Mix 5000 g Carrots 10mm Diced (Frozen) on Qty. Net/gross left empty. '
    'Evidence: GFF242R - Steamed Carrot 10mm Diced V.3.pdf. Draft pending human approval.'
)
NOTE_QAS_HR = (
    'Source: GFF186MC QAS V.6 (issue 6, 22.07.2020 / review 03.06.2025). '
    'Filling GFF003R 100 g. Tray PKLEE001-13 (A2 Pedro pick vs QAS PKESS001-09). '
    'Film PKKMP001-08 missing in new DB — omitted (A5). '
    'Evidence: GFF186MC - G&G  Vegetable Samosa 100g V.6.xlsx. Draft pending human approval.'
)
NOTE_QAS_FG = (
    'SKU named on GFF186MC QAS V.6 (CVSAL-G12T & CVSAL-1G6T). '
    'Sleeve PKTHE006-03 (A3). Box OC0014 / PKCON001-35. '
    'Evidence: GFF186MC - G&G  Vegetable Samosa 100g V.6.xlsx. Draft pending human approval.'
)
NOTE_PEDRO = (
    'No GFF sheet for this stage. Quantities from Pedro tblproducttree. '
    'Yield 0.99 not applied. Draft pending human approval. '
    'Anomalies: docs/Recipe-import-task/anomaly-CVSAL-1G6T.md'
)

CLASS_SNACK = 19
U_UNIT, U_G, U_TRAY = 1, 2, 9005
LOC = dict(steam=223, mix=84, belt=32, fry=34, hr=4, sleeve=5, dispatch=6)

# recipe_code, name, cat, unit, src, dst, gff, chilled, notes
PRODUCTS = (
    ('GFF241R - St', 'Steamed - Potato - Diced - 10mm - 241 - Steaming', 143, U_G, LOC['steam'], LOC['mix'], 'GFF241R-St', False, NOTE_STEAM_P),
    ('GFF242R - St', 'Steamed - Carrot - Diced - 10mm - 242 - Steaming', 143, U_G, LOC['steam'], LOC['mix'], 'GFF242R-St', False, NOTE_STEAM_C),
    ('GFF003R-Mx', 'Vegetable Samosa - 003 - Mixer', 164, U_G, LOC['mix'], LOC['belt'], 'GFF003R-Mx', False, NOTE_PDF),
    ('GFF003R-B-100', 'Vegetable Samosa - 100 grams - 003 - Belt', 167, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_PEDRO),
    ('GFF003R - F - 100', 'Vegetable Samosa - 100 grams - 003 - Frying', 173, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_PEDRO),
    ('CVSAL', 'GG - 1 x Vegetable Samosa - 003R - 100 grams - SQ TRAY | 100 grams', 76, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF186MC', True, NOTE_QAS_HR),
    ('CVSAL-1G6T', 'Gazebo - G&G - Vegetable Samosa | 100G X 6', 127, U_UNIT, LOC['sleeve'], LOC['dispatch'], None, True, NOTE_QAS_FG),
)


def _product(code, name, cat, unit, src, dst, gff, chilled, remarks):
    row, created = Product.objects.get_or_create(
        recipe_code=code,
        defaults={
            'name': name,
            'gff_code': gff,
            'product_class_id': CLASS_SNACK,
            'category_id': cat,
            'unit_id': unit,
            'source_container_id': src,
            'destination_container_id': dst,
            'remarks': remarks,
            'goods_in_type': 'other',
        },
    )
    if not created and remarks and row.remarks != remarks:
        row.remarks = remarks
        row.save(update_fields=['remarks', 'updated_at'])
    ProductTechnical.objects.update_or_create(
        product_id=row.id,
        defaults={'storage_regime': 'chilled' if chilled else 'ambient'},
    )
    ProductFlags.objects.get_or_create(product_id=row.id)
    return row, created


def _draft(product, remarks):
    recipe, _ = get_or_create_recipe_with_draft(
        product.id, name=product.name, remarks=remarks,
        created_by_name='import_pilot_cvsal_1g6t',
    )
    if recipe.remarks != remarks:
        recipe.remarks = remarks
        recipe.save(update_fields=['remarks', 'updated_at'])
    version = recipe.versions.order_by('version_number').first()
    if version.status != RecipeVersionStatus.DRAFT:
        raise RuntimeError(f'{product.recipe_code} version {version.id} is {version.status}, not draft')
    version.remarks = remarks
    version.process_loss = Decimal('1.0000')
    version.save(update_fields=['remarks', 'process_loss', 'updated_at'])
    return version


def _line(version, line_no, child, qty, unit_id, batch=None):
    RecipeComponent.objects.update_or_create(
        recipe_version=version,
        line_no=line_no,
        defaults={
            'component_product': child,
            'quantity': Decimal(str(qty)),
            'unit_id': unit_id,
            'batch_quantity': Decimal(str(batch)) if batch is not None else None,
        },
    )


def _attach(version, path: Path, caption: str):
    if not path.exists():
        return f'missing {path.name}'
    if version.attachments.filter(original_filename=path.name).exists():
        return f'skip {path.name}'
    mime = 'application/pdf' if path.suffix.lower() == '.pdf' else (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    uploaded = SimpleUploadedFile(path.name, path.read_bytes(), content_type=mime)
    upload_attachment(
        version,
        uploaded_file=uploaded,
        kind=RecipeAttachmentKind.OTHER,
        caption=caption,
        uploaded_by_sub='import_pilot_cvsal_1g6t',
    )
    return f'ok {path.name}'


class Command(BaseCommand):
    help = 'Create CVSAL-1G6T draft recipes from Harvi PDFs + Pedro tree. Never activates.'

    def handle(self, *args, **options):
        with transaction.atomic():
            by_code = {p.recipe_code: p for p in Product.objects.filter(recipe_code__in=[
                'VEGFRO-01', 'VEGFRO-02', 'VEGFRO-04', 'VEGFRO-10', 'VEGCHI-02',
                'PASTRY-01', 'SPICE0-02', 'SPICE0-03', 'SPICE0-04', 'SPICE0-06',
                'SPICE0-08', 'SPICE0-66', 'SAUCE0-02', 'SAUCE0-03', 'SAUCE0-05',
                'GFF003R-S', 'PKLEE001-13', 'PKTHE006-03', 'OC0014',
            ])}
            missing = [c for c in (
                'VEGFRO-01', 'VEGCHI-02', 'GFF003R-S', 'PKLEE001-13', 'PKTHE006-03', 'OC0014',
            ) if c not in by_code]
            if missing:
                raise RuntimeError(f'missing products: {missing}')

            created = []
            for spec in PRODUCTS:
                row, was = _product(*spec)
                by_code[row.recipe_code] = row
                created.append(f"{'NEW' if was else 'reuse'} {row.recipe_code} id={row.id}")

            steam_p = by_code['GFF241R - St']
            steam_c = by_code['GFF242R - St']
            spice = by_code['GFF003R-S']
            mixer = by_code['GFF003R-Mx']
            belt = by_code['GFF003R-B-100']
            fry = by_code['GFF003R - F - 100']
            hr = by_code['CVSAL']
            fg = by_code['CVSAL-1G6T']

            ProductPackaging.objects.get_or_create(
                product_id=hr.id, defaults={'pack_weight': Decimal('100')},
            )
            ProductPackaging.objects.update_or_create(
                product_id=fg.id,
                defaults={
                    'items_per_unit': Decimal('6'),
                    'unitary_weight': Decimal('100'),
                    'pack_weight': Decimal('600'),
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

            v_st_p = _draft(steam_p, NOTE_STEAM_P)
            _line(v_st_p, 1, by_code['VEGCHI-02'], 10600, U_G)
            v_st_c = _draft(steam_c, NOTE_STEAM_C)
            _line(v_st_c, 1, by_code['VEGFRO-04'], 5000, U_G)

            v_sp = _draft(spice, NOTE_PDF)
            v_sp.batch_quantity = Decimal('11040')
            v_sp.batch_unit_id = U_G
            v_sp.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            # existing 7 PDF lines kept (tamarind = SPICE0-66 / 344)

            v_mx = _draft(mixer, NOTE_PDF)
            v_mx.batch_quantity = Decimal('186080')
            v_mx.batch_unit_id = U_G
            v_mx.save(update_fields=['batch_quantity', 'batch_unit_id', 'updated_at'])
            _line(v_mx, 1, steam_p, 96000, U_G, 96000)
            _line(v_mx, 2, by_code['VEGFRO-01'], 48000, U_G, 48000)
            _line(v_mx, 3, steam_c, 16000, U_G, 16000)
            _line(v_mx, 4, by_code['VEGFRO-02'], 6400, U_G, 6400)
            _line(v_mx, 5, by_code['SAUCE0-03'], 3840, U_G, 3840)
            _line(v_mx, 6, by_code['SAUCE0-02'], 3200, U_G, 3200)
            _line(v_mx, 7, by_code['VEGFRO-10'], 1600, U_G, 1600)
            _line(v_mx, 8, spice, 11040, U_G, 11040)

            v_b = _draft(belt, NOTE_PEDRO)
            _line(v_b, 1, mixer, '80.2', U_G)
            _line(v_b, 2, by_code['PASTRY-01'], 1, U_UNIT)

            v_f = _draft(fry, NOTE_PEDRO)
            _line(v_f, 1, belt, 1, U_UNIT)

            v_hr = _draft(hr, NOTE_QAS_HR)
            _line(v_hr, 1, fry, 1, U_UNIT)
            _line(v_hr, 2, by_code['PKLEE001-13'], 1, U_TRAY)

            v_fg = _draft(fg, NOTE_QAS_FG)
            _line(v_fg, 1, hr, 6, U_UNIT)
            _line(v_fg, 2, by_code['PKTHE006-03'], 6, U_UNIT)
            _line(v_fg, 3, by_code['OC0014'], 1, U_UNIT)

            for p in (steam_p, steam_c, spice, mixer, belt, fry, hr, fg):
                sync_has_recipe(p.id)

            att = [
                _attach(v_sp, PDF_SPICE, 'GFF003R-S V.14 spice mix evidence'),
                _attach(v_mx, PDF_MIXER, 'GFF003R V.17 mixer mix evidence'),
                _attach(v_st_p, PDF_STEAM_P, 'GFF241R V.2 steamed potato evidence'),
                _attach(v_st_c, PDF_STEAM_C, 'GFF242R V.3 steamed carrot evidence'),
                _attach(v_hr, QAS_HR, 'GFF186MC V.6 packed-item QAS evidence'),
                _attach(v_fg, QAS_HR, 'GFF186MC V.6 SKU named on QAS'),
            ]

        self.stdout.write('\n'.join(created))
        self.stdout.write('attachments: ' + ', '.join(att))
        self.stdout.write(self.style.SUCCESS(
            f'drafts spice={v_sp.id} mixer={v_mx.id} belt={v_b.id} fry={v_f.id} hr={v_hr.id} fg={v_fg.id}'
        ))
