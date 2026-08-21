"""Draft-only CCSAL-1G6T tree. PDF grams. Does not activate.

  python manage.py import_pilot_ccsal_1g6t
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import (
    Product,
    ProductAudit,
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
    / 'GFF004R CHICKEN SAMOSA - V.20.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF004R-S CHICKEN SAMOSA - SPICES V.16.pdf'
)
QAS_HR = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Gazebo' / 'Grab & Go-QAS'
    / 'GFF185MC - G&G Chicken Tikka Samosa 100g V.6.xlsx'
)

STAMP = 'System Admin'
NOTE_SPICE = 'Draft from the signed spice sheet. Amounts are grams from GFF004R-S issue 16.'
NOTE_MIX = 'Draft from the signed mix sheet. Amounts are grams from GFF004R issue 20.'
NOTE_BELT = 'No mix sheet for this step. Fill weight is from the live factory recipe: 69.55 g mix plus one pastry.'
NOTE_FRY = 'No mix sheet for this step. One fried piece from the belt.'
NOTE_HR = (
    'Pack from GFF185MC QAS. Tray taken from the factory recipe. '
    'The 240 mm K-peel film is not in the new catalogue, so it was left off.'
)
NOTE_FG = (
    'Finished pack of six. Sleeve is the nearest chicken tikka G&G label in the new catalogue. '
    'Box is the G&G six-tub case.'
)
NOTE_WATER = 'Water used in the mix. Added because it was missing from the new catalogue.'

CLASS_SNACK = 19
U_UNIT, U_G, U_TRAY = 1, 2, 9005
LOC = dict(mix=84, belt=32, fry=34, hr=4, sleeve=5, dispatch=6, spice=15, gazebo=11, lwr=3, stores=8)

PRODUCTS = (
    ('AFFINITY-S1', 'Water - STEP 1', 16, U_G, LOC['gazebo'], LOC['lwr'], None, False, NOTE_WATER),
    ('GFF004R-S', 'Chicken Samosa - 004 - Spice', 6, U_G, LOC['spice'], LOC['mix'], 'GFF004R-S', False, NOTE_SPICE),
    ('GFF004R-Mx', 'Chicken Samosa - 004 - Mixer', 164, U_G, LOC['mix'], LOC['belt'], 'GFF004R-Mx', False, NOTE_MIX),
    ('GFF004R-B-100', 'Chicken Samosa - 100 grams - 004 - Belt', 167, U_UNIT, LOC['belt'], LOC['fry'], None, False, NOTE_BELT),
    ('GFF004R - F - 100', 'Chicken Samosa - 100 grams - 004 - Frying', 173, U_UNIT, LOC['fry'], LOC['hr'], None, False, NOTE_FRY),
    ('CCSAL', 'GG - 1 x Chicken Samosa - 004 R - 100 grams - SQ TRAY | 100 grams', 76, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF185MC', True, NOTE_HR),
    ('CCSAL-1G6T', 'Gazebo - G&G - Chicken Tikka Samosa | 100G X 6', 127, U_UNIT, LOC['sleeve'], LOC['dispatch'], None, True, NOTE_FG),
)


def _stamp(product, action, after):
    row, _ = ProductAudit.objects.get_or_create(product_id=product.id)
    events = list(row.timeline_events or [])
    events.append({
        'at': datetime.now(timezone.utc).isoformat(),
        'entity': 'product',
        'action': action,
        'actor_sub': None,
        'actor_name': STAMP,
        'actor_email': None,
        'request_method': 'IMPORT',
        'request_path': 'manage.py import_pilot_ccsal_1g6t',
        'source_workstation_ip': None,
        'source_workstation': STAMP,
        'before_json': None,
        'after_json': after,
        'changed_fields': sorted((after or {}).keys()),
    })
    row.timeline_events = events
    row.lan_username = STAMP
    row.save(update_fields=['timeline_events', 'lan_username', 'updated_at'])


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
    _stamp(row, 'create' if created else 'update', {
        'recipe_code': code, 'remarks': remarks, 'gff_code': gff,
    })
    return row, created


def _draft(product, remarks):
    recipe, _ = get_or_create_recipe_with_draft(
        product.id, name=product.name, remarks=remarks,
        created_by_name=STAMP,
    )
    if recipe.remarks != remarks:
        recipe.remarks = remarks
        recipe.save(update_fields=['remarks', 'updated_at'])
    if recipe.created_by_name != STAMP:
        recipe.created_by_name = STAMP
        recipe.save(update_fields=['created_by_name', 'updated_at'])
    version = recipe.versions.order_by('version_number').first()
    if version.status != RecipeVersionStatus.DRAFT:
        raise RuntimeError(f'{product.recipe_code} version {version.id} is {version.status}, not draft')
    version.remarks = remarks
    version.process_loss = Decimal('1.0000')
    version.created_by_name = STAMP
    version.save(update_fields=['remarks', 'process_loss', 'created_by_name', 'updated_at'])
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
        uploaded_by_sub=STAMP,
    )
    return f'ok {path.name}'


class Command(BaseCommand):
    help = 'Create CCSAL-1G6T draft recipes from Harvi PDFs + Pedro tree. Never activates.'

    def handle(self, *args, **options):
        with transaction.atomic():
            by_code = {p.recipe_code: p for p in Product.objects.filter(recipe_code__in=[
                'PROTEIN-02', 'PROTEIN-12', 'VEGFRO-01', 'VEGFRO-02', 'INGRAD-06',
                'SAUCE0-02', 'SPICE0-01', 'SPICE0-03', 'SPICE0-04', 'SPICE0-08',
                'PASTRY-01', 'PKLEE001-13', 'PKTHE006-01', 'OC0014',
            ])}
            missing = [c for c in (
                'PROTEIN-02', 'PROTEIN-12', 'INGRAD-06', 'SPICE0-01',
                'PKLEE001-13', 'PKTHE006-01', 'OC0014', 'PASTRY-01',
            ) if c not in by_code]
            if missing:
                raise RuntimeError(f'missing products: {missing}')

            created = []
            for spec in PRODUCTS:
                row, was = _product(*spec)
                by_code[row.recipe_code] = row
                created.append(f"{'NEW' if was else 'reuse'} {row.recipe_code} id={row.id}")

            water = by_code['AFFINITY-S1']
            spice = by_code['GFF004R-S']
            mixer = by_code['GFF004R-Mx']
            belt = by_code['GFF004R-B-100']
            fry = by_code['GFF004R - F - 100']
            hr = by_code['CCSAL']
            fg = by_code['CCSAL-1G6T']

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

            v_sp = _draft(spice, NOTE_SPICE)
            _line(v_sp, 1, by_code['SPICE0-03'], 1212, U_G)
            _line(v_sp, 2, by_code['SPICE0-04'], 505, U_G)
            _line(v_sp, 3, by_code['SPICE0-08'], 379, U_G)
            _line(v_sp, 4, by_code['SPICE0-01'], 11010, U_G)

            v_mx = _draft(mixer, NOTE_MIX)
            _line(v_mx, 1, by_code['PROTEIN-02'], 25000, U_G)
            _line(v_mx, 2, by_code['PROTEIN-12'], 25000, U_G)
            _line(v_mx, 3, by_code['VEGFRO-02'], 50000, U_G)
            _line(v_mx, 4, by_code['VEGFRO-01'], 50000, U_G)
            _line(v_mx, 5, by_code['INGRAD-06'], 25000, U_G)
            _line(v_mx, 6, water, 20000, U_G)
            _line(v_mx, 7, by_code['SAUCE0-02'], 7500, U_G)
            _line(v_mx, 8, spice, 13106, U_G)

            v_b = _draft(belt, NOTE_BELT)
            _line(v_b, 1, mixer, '69.5503', U_G)
            _line(v_b, 2, by_code['PASTRY-01'], 1, U_UNIT)

            v_f = _draft(fry, NOTE_FRY)
            _line(v_f, 1, belt, 1, U_UNIT)

            v_hr = _draft(hr, NOTE_HR)
            _line(v_hr, 1, fry, 1, U_UNIT)
            _line(v_hr, 2, by_code['PKLEE001-13'], 1, U_TRAY)

            v_fg = _draft(fg, NOTE_FG)
            _line(v_fg, 1, hr, 6, U_UNIT)
            _line(v_fg, 2, by_code['PKTHE006-01'], 6, U_UNIT)
            _line(v_fg, 3, by_code['OC0014'], 1, U_UNIT)

            for p in (water, spice, mixer, belt, fry, hr, fg):
                sync_has_recipe(p.id)

            att = [
                _attach(v_sp, PDF_SPICE, 'GFF004R-S V.16 spice mix sheet'),
                _attach(v_mx, PDF_MIXER, 'GFF004R V.20 mixer mix sheet'),
                _attach(v_hr, QAS_HR, 'GFF185MC V.6 packed-item QAS'),
                _attach(v_fg, QAS_HR, 'GFF185MC V.6 names this SKU'),
            ]

        self.stdout.write('\n'.join(created))
        self.stdout.write('attachments: ' + ', '.join(att))
        self.stdout.write(self.style.SUCCESS(
            f'drafts spice={v_sp.id} mixer={v_mx.id} belt={v_b.id} fry={v_f.id} hr={v_hr.id} fg={v_fg.id}'
        ))
