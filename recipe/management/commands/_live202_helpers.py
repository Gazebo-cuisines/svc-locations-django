"""
Shared helpers for live-202 batch recipe import commands.

Usage in import scripts:
    from recipe.management.commands._live202_helpers import (
        U_G, U_UNIT, CAT_SPICE, CAT_SAUCE, CAT_PACKED, CAT_FG, CAT_RM,
        LOC, make_product, make_draft, add_line, activate, require_products,
        sync_has_recipe,
    )
"""
import pymysql
pymysql.install_as_MySQLdb()

from decimal import Decimal

from django.db import transaction

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductFlags,
    ProductYield,
    Range,
    Unit,
)
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from recipe.utils import activate_version, sync_has_recipe  # noqa: F401

# ── Units ─────────────────────────────────────────────────────────────────────
def _unit(name: str) -> Unit:
    obj, _ = Unit.objects.get_or_create(name=name)
    return obj


def U_G() -> Unit:    return _unit('g')
def U_UNIT() -> Unit: return _unit('Each')


# ── Location lookup (by external_code, created lazily if needed) ──────────────
_LOC_CACHE: dict[str, Location] = {}

_LOC_SPECS = {
    'spice':  ('Spice Room',          'LR-SPICE'),
    'lwr':    ('Low Risk',            'LR-MIX'),
    'belt':   ('Belts',               'LR-BELT'),
    'fry':    ('Fryers',              'LR-FRY'),
    'hr':     ('High Risk',           'HR-PACK'),
    'steam':  ('Steam Room',          'HR-STEAM'),
    'deli':   ('Deli',                'LR-DELI'),
    'rm':     ('Raw Materials Store', 'RM-STORE'),
    'disp':   ('Dispatch',            'DISP'),
}

# These IDs are looked up lazily from the DB rather than hardcoded.
def LOC(key: str) -> Location:
    if key not in _LOC_CACHE:
        ext = _LOC_SPECS[key][1]
        name = _LOC_SPECS[key][0]
        obj = Location.objects.filter(external_code=ext).first()
        if obj is None:
            # Try by name
            obj = Location.objects.filter(name__icontains=name.split()[0]).first()
        if obj is None:
            raise RuntimeError(
                f'Location {key!r} (ext={ext}) not found in DB. '
                'Check Location table.'
            )
        _LOC_CACHE[key] = obj
    return _LOC_CACHE[key]


# ── Category / ProductClass helpers ──────────────────────────────────────────
def _cat(name: str) -> Category:
    obj, _ = Category.objects.get_or_create(name=name)
    return obj

def _cls(name: str) -> ProductClass:
    obj, _ = ProductClass.objects.get_or_create(name=name)
    return obj

def CAT_SPICE()  -> Category: return _cat('Spice Mix')
def CAT_SAUCE()  -> Category: return _cat('Sauce')
def CAT_PACKED() -> Category: return _cat('Packed Item')
def CAT_FG()     -> Category: return _cat('Finished Good')
def CAT_RM()     -> Category: return _cat('Raw Material')
def CLS_INT()    -> ProductClass: return _cls('Intermediate')
def CLS_FG()     -> ProductClass: return _cls('Finished Good')
def CLS_RM()     -> ProductClass: return _cls('Raw Material')


# ── Product helpers ───────────────────────────────────────────────────────────
def make_product(
    recipe_code: str,
    name: str,
    category: Category,
    unit: Unit,
    src_loc: Location,
    dst_loc: Location,
    gff_code: str,
    is_sales: bool,
    remarks: str,
    product_class: ProductClass | None = None,
) -> tuple[Product, bool]:
    """get_or_create a Product; always ensures ProductFlags and ProductYield exist."""
    p_cls = product_class or CLS_INT()
    p, created = Product.objects.get_or_create(
        recipe_code=recipe_code,
        defaults=dict(
            name=name,
            category=category,
            unit=unit,
            purchasing_unit=unit,
            source_container=src_loc,
            destination_container=dst_loc,
            gff_code=gff_code,
            is_active=True,
            is_downtime=False,
            remarks=remarks,
            product_class=p_cls,
        ),
    )
    ProductFlags.objects.get_or_create(
        product=p,
        defaults={'has_recipe': False, 'is_sales_item': is_sales},
    )
    ProductYield.objects.get_or_create(
        product=p,
        defaults={'yield_factor': Decimal('1.0000'), 'yield_factor_auto': Decimal('1.0000')},
    )
    return p, created


def make_draft(product: Product, remarks: str) -> RecipeVersion:
    """Create Recipe + a single DRAFT RecipeVersion (v1) if none exist."""
    recipe, _ = Recipe.objects.get_or_create(
        product=product,
        defaults={'name': product.name, 'remarks': remarks},
    )
    version, _ = RecipeVersion.objects.get_or_create(
        recipe=recipe,
        version_number=1,
        defaults=dict(
            status=RecipeVersionStatus.DRAFT,
            process_loss=Decimal('1.0000'),
            batch_quantity=None,
            batch_unit=U_G(),
            sum_batch_quantity=None,
            location=product.destination_container,
        ),
    )
    return version


def get_or_create_version(product: Product, batch_qty, location: Location, remarks: str = '') -> RecipeVersion:
    """
    Return the existing draft v1 RecipeVersion for product, or create one.
    Also creates the Recipe if needed.
    """
    recipe, _ = Recipe.objects.get_or_create(
        product=product,
        defaults={'name': product.name, 'remarks': remarks or product.remarks or ''},
    )
    version, _ = RecipeVersion.objects.get_or_create(
        recipe=recipe,
        version_number=1,
        defaults=dict(
            status=RecipeVersionStatus.DRAFT,
            process_loss=Decimal('1.0000'),
            batch_quantity=Decimal(str(batch_qty)) if batch_qty else None,
            batch_unit=U_G(),
            sum_batch_quantity=Decimal(str(batch_qty)) if batch_qty else None,
            location=location,
        ),
    )
    return version


def add_line(version: RecipeVersion, line_no: int, component: Product, qty: int | float, unit: Unit) -> RecipeComponent:
    """Add a RecipeComponent line to a version (skip if already exists for that line_no)."""
    rc, _ = RecipeComponent.objects.get_or_create(
        recipe_version=version,
        line_no=line_no,
        defaults=dict(
            component_product=component,
            quantity=Decimal(str(qty)),
            unit=unit,
            batch_quantity=Decimal(str(qty)),
        ),
    )
    return rc


def activate(version: RecipeVersion) -> None:
    """Activate a version and sync has_recipe flag."""
    activate_version(version)
    sync_has_recipe(version.recipe.product_id)


def require_products(*codes: str) -> dict[str, Product]:
    """
    Fetch existing products by recipe_code. Raises if any are missing.
    Returns dict: recipe_code -> Product.
    """
    found = {p.recipe_code: p for p in Product.objects.filter(recipe_code__in=codes)}
    missing = [c for c in codes if c not in found]
    if missing:
        raise RuntimeError(f'Missing RM products: {missing}. Add them to the ERP first.')
    return found
