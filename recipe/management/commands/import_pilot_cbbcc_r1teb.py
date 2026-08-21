"""Draft-only CBBCC-R1TEB tree. PDF grams. Does not activate.

  python manage.py import_pilot_cbbcc_r1teb
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
    / 'GFF394R - Bang Bang Cauliflower V.3.pdf'
)
PDF_SPICE = (
    DOCS / 'harvi' / '2 SPICES' / 'Signed copy SPICE'
    / 'GFF394R-S - Bang Bang Cauliflower - Spices V.3.pdf'
)
QAS_HR = (
    DOCS / 'harvi' / 'HIGH RISK-QAS' / 'Booths Xmas'
    / 'GFF333MC - BOOTHS - XMAS - Bang Bang Cauliflower & Tamarind Dip 200g V.5.xlsx'
)

STAMP = 'System Admin'
NOTE_SPICE = (
    'Draft from the signed spice sheet. Amounts are grams from GFF394R-S issue 3. '
    'Please add garlic granules 240 g on this recipe with the catalogue code.'
)
NOTE_MIX = 'Draft from the signed mix sheet. Amounts are grams from GFF394R issue 3.'
NOTE_HR = (
    'Pack from GFF333MC QAS: 160 g cauliflower mix, SK4 tray and pot. '
    'Please add tamarind dip 40 g on this recipe with the catalogue code. '
    'Film is named on the QAS but the factory packed item has no film quantity, so film was left off. '
    'Sleeve L780-03 is not in the new catalogue, so it was left off.'
)
NOTE_FG = (
    'Booths case of one. Box is the GMB2 case. Sleeve is not in the new catalogue.'
)

CLASS_SNACK = 19
U_UNIT, U_G, U_TRAY = 1, 2, 9005
LOC = dict(mix=84, fry=34, hr=4, sleeve=5, dispatch=6, spice=15, marin=85, gazebo=11, lwr=3, stores=8)

PRODUCTS = (
    ('GFF394R-S', 'Bang Bang Cauliflower - 394 - Spices', 137, U_G, LOC['spice'], LOC['marin'], 'GFF394R-S', False, NOTE_SPICE),
    ('GFF394R', 'Bang Bang Cauliflower - 394 - Marination', 174, U_G, LOC['marin'], LOC['fry'], 'GFF394R', False, NOTE_MIX),
    ('CBBCC', 'SK4 - Bang Bang Cauliflower & Tamarind Dip 200g', 76, U_UNIT, LOC['hr'], LOC['sleeve'], 'GFF333MC', True, NOTE_HR),
    ('CBBCC-R1TEB', 'Booths - Bang Bang Cauliflower with Tamarind | 200G x 1', 130, U_UNIT, LOC['sleeve'], LOC['dispatch'], None, True, NOTE_FG),
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
        'request_path': 'manage.py import_pilot_cbbcc_r1teb',
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
    if not created and gff and row.gff_code != gff:
        row.gff_code = gff
        row.save(update_fields=['gff_code', 'updated_at'])
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
    help = 'Create CBBCC-R1TEB draft recipes from Harvi PDFs + QAS. Never activates.'

    def handle(self, *args, **options):
        with transaction.atomic():
            need = [
                'VEGCHI-11', 'AFFINITY-S1', 'INGRAD-17', 'INGRAD-20', 'INGRAD-21',
                'SPICE0-02', 'SPICE0-08', 'SPICE0-53', 'SPICE0-24', 'SPICE0-25',
                'SPICE0-11', 'SPICE0-58', 'PKESS001-07', 'PKJEN001-03', 'OC005',
            ]
            by_code = {p.recipe_code: p for p in Product.objects.filter(recipe_code__in=need)}
            missing = [c for c in need if c not in by_code]
            if missing:
                raise RuntimeError(f'missing products: {missing}')

            created = []
            for spec in PRODUCTS:
                row, was = _product(*spec)
                by_code[row.recipe_code] = row
                created.append(f"{'NEW' if was else 'reuse'} {row.recipe_code} id={row.id}")

            spice = by_code['GFF394R-S']
            mix = by_code['GFF394R']
            hr = by_code['CBBCC']
            fg = by_code['CBBCC-R1TEB']

            ProductPackaging.objects.get_or_create(
                product_id=hr.id, defaults={'pack_weight': Decimal('200')},
            )
            ProductPackaging.objects.update_or_create(
                product_id=fg.id,
                defaults={
                    'items_per_unit': Decimal('1'),
                    'unitary_weight': Decimal('200'),
                    'pack_weight': Decimal('200'),
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
            RecipeComponent.objects.filter(recipe_version=v_sp).delete()
            _line(v_sp, 1, by_code['INGRAD-21'], 2000, U_G)
            _line(v_sp, 2, by_code['INGRAD-20'], 2000, U_G)
            _line(v_sp, 3, by_code['SPICE0-02'], 200, U_G)
            _line(v_sp, 4, by_code['SPICE0-08'], 120, U_G)
            _line(v_sp, 5, by_code['SPICE0-53'], 100, U_G)
            _line(v_sp, 6, by_code['SPICE0-24'], 100, U_G)
            _line(v_sp, 7, by_code['SPICE0-25'], 60, U_G)
            _line(v_sp, 8, by_code['SPICE0-11'], 40, U_G)
            _line(v_sp, 9, by_code['SPICE0-58'], 40, U_G)

            v_mx = _draft(mix, NOTE_MIX)
            _line(v_mx, 1, by_code['VEGCHI-11'], 20000, U_G)
            _line(v_mx, 2, by_code['AFFINITY-S1'], 10000, U_G)
            _line(v_mx, 3, by_code['INGRAD-17'], 2000, U_G)
            _line(v_mx, 4, spice, 4900, U_G)

            v_hr = _draft(hr, NOTE_HR)
            RecipeComponent.objects.filter(recipe_version=v_hr).delete()
            _line(v_hr, 1, mix, 160, U_G)
            _line(v_hr, 2, by_code['PKESS001-07'], 1, U_TRAY)
            _line(v_hr, 3, by_code['PKJEN001-03'], 1, U_UNIT)

            v_fg = _draft(fg, NOTE_FG)
            _line(v_fg, 1, hr, 1, U_UNIT)
            _line(v_fg, 2, by_code['OC005'], 1, U_UNIT)

            for p in (spice, mix, hr, fg):
                sync_has_recipe(p.id)

            att = [
                _attach(v_sp, PDF_SPICE, 'GFF394R-S V.3 spice mix sheet'),
                _attach(v_mx, PDF_MIXER, 'GFF394R V.3 mix sheet'),
                _attach(v_hr, QAS_HR, 'GFF333MC V.5 packed-item QAS'),
                _attach(v_fg, QAS_HR, 'GFF333MC V.5 names this SKU'),
            ]

        self.stdout.write('\n'.join(created))
        self.stdout.write('attachments: ' + ', '.join(att))
        self.stdout.write(self.style.SUCCESS(
            f'drafts spice={v_sp.id} mix={v_mx.id} hr={v_hr.id} fg={v_fg.id}'
        ))
