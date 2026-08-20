"""Shared helpers for live-202 batch import commands.

Usage:
    from ._live202_helpers import STAMP, stamp, make_product, make_draft, add_line, attach_file
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile

from product.models import (
    Product, ProductAudit, ProductFlags, ProductPackaging,
    ProductShelfLife, ProductTechnical,
)
from recipe.attachments import upload_attachment
from recipe.models import RecipeAttachmentKind, RecipeComponent, RecipeVersionStatus
from recipe.utils import get_or_create_recipe_with_draft, sync_has_recipe

STAMP = 'System Admin'
DOCS = Path(__file__).resolve().parents[3] / 'docs' / 'Recipe-import-task'

U_UNIT, U_G, U_M, U_TRAY = 1, 2, 3, 9005
CLASS_SNACK = 19
CLASS_READY = 4   # ready meal product class

# Container IDs (from pilot import_pilot_cvsal_1g6t.py)
LOC = dict(
    steam=223, lwr=84, belt=32, fry=34,
    hr=4, sleeve=5, dispatch=6,
    stores=8, spice=15, marin=85, gazebo=11,
)


def stamp(product, action, after):
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
        'request_path': 'manage.py live202 batch',
        'source_workstation_ip': None,
        'source_workstation': STAMP,
        'before_json': None,
        'after_json': after,
        'changed_fields': sorted((after or {}).keys()),
    })
    row.timeline_events = events
    row.lan_username = STAMP
    row.save(update_fields=['timeline_events', 'lan_username', 'updated_at'])


def make_product(code, name, cat, unit, src, dst, gff, chilled, remarks, product_class=CLASS_SNACK):
    row, created = Product.objects.get_or_create(
        recipe_code=code,
        defaults={
            'name': name,
            'gff_code': gff,
            'product_class_id': product_class,
            'category_id': cat,
            'unit_id': unit,
            'source_container_id': src,
            'destination_container_id': dst,
            'remarks': remarks,
            'goods_in_type': 'other',
        },
    )
    if not created:
        changed = False
        if remarks and row.remarks != remarks:
            row.remarks = remarks; changed = True
        if gff and row.gff_code != gff:
            row.gff_code = gff; changed = True
        if changed:
            row.save(update_fields=['remarks', 'gff_code', 'updated_at'])
    ProductTechnical.objects.update_or_create(
        product_id=row.id,
        defaults={'storage_regime': 'chilled' if chilled else 'ambient'},
    )
    ProductFlags.objects.get_or_create(product_id=row.id)
    stamp(row, 'create' if created else 'update', {'recipe_code': code, 'remarks': remarks})
    return row, created


def make_draft(product, remarks):
    recipe, _ = get_or_create_recipe_with_draft(
        product.id, name=product.name, remarks=remarks, created_by_name=STAMP,
    )
    if recipe.remarks != remarks:
        recipe.remarks = remarks
        recipe.save(update_fields=['remarks', 'updated_at'])
    if recipe.created_by_name != STAMP:
        recipe.created_by_name = STAMP
        recipe.save(update_fields=['created_by_name', 'updated_at'])
    version = recipe.versions.order_by('version_number').first()
    if version.status != RecipeVersionStatus.DRAFT:
        raise RuntimeError(f'{product.recipe_code} v{version.id} is {version.status}')
    version.remarks = remarks
    version.process_loss = Decimal('1.0000')
    version.created_by_name = STAMP
    version.save(update_fields=['remarks', 'process_loss', 'created_by_name', 'updated_at'])
    return version


def add_line(version, line_no, child, qty, unit_id):
    RecipeComponent.objects.update_or_create(
        recipe_version=version,
        line_no=line_no,
        defaults={
            'component_product': child,
            'quantity': Decimal(str(qty)),
            'unit_id': unit_id,
            'batch_quantity': None,
        },
    )


def clear_lines(version):
    RecipeComponent.objects.filter(recipe_version=version).delete()


def attach_file(version, path: Path, caption: str):
    if not path.exists():
        return f'missing {path.name}'
    if version.attachments.filter(original_filename=path.name).exists():
        return f'skip {path.name}'
    mime = 'application/pdf' if path.suffix.lower() == '.pdf' else (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    uploaded = SimpleUploadedFile(path.name, path.read_bytes(), content_type=mime)
    upload_attachment(version, uploaded_file=uploaded, kind=RecipeAttachmentKind.OTHER,
                      caption=caption, uploaded_by_sub=STAMP)
    return f'ok {path.name}'


def require_products(*codes):
    """Return dict code->Product, raising RuntimeError if any missing."""
    found = {p.recipe_code: p for p in Product.objects.filter(recipe_code__in=codes)}
    missing = [c for c in codes if c not in found]
    if missing:
        raise RuntimeError(f'missing from catalogue: {missing}')
    return found
