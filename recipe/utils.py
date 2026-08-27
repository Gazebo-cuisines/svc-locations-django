from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from product.category_images import category_image_url
from product.models import Product, ProductFlags
from product.product_images import product_photos
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus

MAX_TREE_DEPTH = 20
_BATCH_BOM_MIN = Decimal('1000')
_SMALL_PROCESS_BOM_MIN = Decimal('100')


def is_process_batch_recipe(recipe_code: str | None, name: str | None) -> bool:
    """Spice / cook / steam / mix SKUs — BOM grams are for one batch, not one unit."""
    code = recipe_code or ''
    label = name or ''
    if code.endswith(('-S', '-C', '-Mx')) or ' - St' in code:
        return True
    return any(
        marker in label
        for marker in (
            ' - Spice',
            ' - Spices',
            ' - Cooking',
            ' - Steaming',
            ' - Mix',
        )
    )


def batch_scale_denom(
    component_qty: Decimal,
    *,
    batch_quantity: Decimal | None,
    bom_sum: Decimal | None,
    process_batch: bool = False,
) -> Decimal | None:
    total = bom_sum if bom_sum is not None else component_qty
    if total >= _BATCH_BOM_MIN or (
        process_batch and total >= _SMALL_PROCESS_BOM_MIN
    ):
        if batch_quantity is not None and batch_quantity > 0:
            return batch_quantity
        if total > 0:
            return total
    return None


def scaled_child_net(
    parent_gross: Decimal,
    component_qty: Decimal,
    *,
    yield_factor: Decimal = Decimal('1'),
    batch_quantity: Decimal | None = None,
    bom_sum: Decimal | None = None,
    process_batch: bool = False,
) -> Decimal:
    """Child qty from parent gross.

    Pack/belt/fry BOMs are per parent unit (counts, ~80g piece). Mix/spice/cook
    BOMs are grams-for-the-batch (line totals typically >= 1000, or any process
    recipe whose BOM is at least 100 g). Scale those by batch_quantity, or by
    the BOM sum when batch_quantity is unset.
    Do not scale merely because batch_quantity is set — that field is also
    min-batch for chain-net.
    """
    yf = yield_factor if yield_factor and yield_factor > 0 else Decimal('1')
    denom = batch_scale_denom(
        component_qty,
        batch_quantity=batch_quantity,
        bom_sum=bom_sum,
        process_batch=process_batch,
    )
    if denom:
        return parent_gross * component_qty / denom / yf
    return parent_gross * component_qty / yf


def apply_bom_batch_quantity(version: RecipeVersion) -> list[str]:
    """Cache BOM sum. On spice/cook/steam/mix, set batch_quantity from it."""
    components = list(version.components.all())
    bom_sum = sum((c.quantity for c in components), Decimal('0'))
    fields: list[str] = []
    cached = bom_sum if bom_sum > 0 else None
    if version.sum_batch_quantity != cached:
        version.sum_batch_quantity = cached
        fields.append('sum_batch_quantity')
    product = version.recipe.product
    if (
        is_process_batch_recipe(product.recipe_code, product.name)
        and bom_sum >= _SMALL_PROCESS_BOM_MIN
    ):
        if version.batch_quantity != bom_sum:
            version.batch_quantity = bom_sum
            fields.append('batch_quantity')
        if version.batch_unit_id is None and components:
            version.batch_unit_id = components[0].unit_id
            fields.append('batch_unit_id')
    return fields


def sync_active_bom_batch_quantities() -> int:
    """Recompute batch_quantity from BOM on every active mix/spice/cook/steam version."""
    versions = (
        RecipeVersion.objects.filter(status=RecipeVersionStatus.ACTIVE)
        .select_related('recipe__product')
        .prefetch_related('components')
        .order_by('id')
    )
    updated = 0
    for version in versions:
        fields = apply_bom_batch_quantity(version)
        if not fields:
            continue
        version.save(update_fields=[*fields, 'updated_at'])
        updated += 1
    return updated


class RecipeValidationError(ValueError):
    """Raised when a recipe rule is violated."""


def assert_not_self_loop(*, parent_product_id: int, component_product_id: int) -> None:
    """Reject component_product_id == recipe.product_id."""
    if parent_product_id == component_product_id:
        raise RecipeValidationError(
            'A recipe cannot include its own product as a component.'
        )


def next_version_number(recipe: Recipe) -> int:
    last = recipe.versions.order_by('-version_number').first()
    return (last.version_number + 1) if last else 1


@transaction.atomic
def get_or_create_recipe_with_draft(
    product_id: int,
    *,
    name=None,
    remarks=None,
    created_by_sub=None,
    created_by_name=None,
) -> tuple[Recipe, bool]:
    """Resolve recipe for a product, creating it plus an empty draft v1 if absent."""
    product_name = Product.objects.get(pk=product_id).name
    defaults = {'name': name or product_name, 'remarks': remarks}
    if created_by_sub:
        defaults['created_by_sub'] = created_by_sub
        defaults['created_by_name'] = created_by_name
    recipe, created = Recipe.objects.select_for_update().get_or_create(
        product_id=product_id,
        defaults=defaults,
    )
    if not recipe.versions.exists():
        RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.DRAFT,
            created_by_sub=created_by_sub,
            created_by_name=created_by_name,
        )
        created = True
    return recipe, created


@transaction.atomic
def clone_version(
    source: RecipeVersion,
    *,
    created_by_sub=None,
    created_by_name=None,
) -> RecipeVersion:
    """New draft on the same recipe with source header fields + all component lines."""
    recipe = Recipe.objects.select_for_update().get(pk=source.recipe_id)
    clone = RecipeVersion.objects.create(
        recipe=recipe,
        version_number=next_version_number(recipe),
        status=RecipeVersionStatus.DRAFT,
        process_loss=source.process_loss,
        batch_quantity=source.batch_quantity,
        batch_unit_id=source.batch_unit_id,
        sum_batch_quantity=source.sum_batch_quantity,
        sum_net_quantity=source.sum_net_quantity,
        sum_gross_quantity=source.sum_gross_quantity,
        location_id=source.location_id,
        remarks=source.remarks,
        created_by_sub=created_by_sub,
        created_by_name=created_by_name,
    )
    RecipeComponent.objects.bulk_create([
        RecipeComponent(
            recipe_version=clone,
            line_no=component.line_no,
            component_product_id=component.component_product_id,
            quantity=component.quantity,
            unit_id=component.unit_id,
            batch_quantity=component.batch_quantity,
            gross_batch_quantity=component.gross_batch_quantity,
            step_instructions=component.step_instructions,
            is_implicit=component.is_implicit,
        )
        for component in source.components.all()
    ])
    sync_has_recipe(recipe.product_id)
    return clone


def sync_has_recipe(product_id: int) -> None:
    """Set product_flags.has_recipe from whether any recipe components exist."""
    has_recipe = RecipeComponent.objects.filter(
        recipe_version__recipe__product_id=product_id,
    ).exists()
    ProductFlags.objects.update_or_create(
        product_id=product_id,
        defaults={'has_recipe': has_recipe},
    )


@transaction.atomic
def activate_version(version: RecipeVersion) -> RecipeVersion:
    """
    Make version active; retire any prior active version for the same recipe.
    Keeps history (no wipe of component lines).
    """
    locked = (
        RecipeVersion.objects.select_for_update(of=('self',))
        .select_related('recipe__product')
        .prefetch_related('components')
        .filter(recipe_id=version.recipe_id)
    )
    target = locked.get(pk=version.pk)
    locked.filter(status=RecipeVersionStatus.ACTIVE).exclude(pk=target.pk).update(
        status=RecipeVersionStatus.RETIRED,
    )
    fields = apply_bom_batch_quantity(target)
    if target.status != RecipeVersionStatus.ACTIVE:
        target.status = RecipeVersionStatus.ACTIVE
        target.activated_at = timezone.now()
        fields.extend(['status', 'activated_at'])
    if fields:
        target.save(update_fields=[*fields, 'updated_at'])
    return target


def _dec(value):
    return str(value) if value is not None else None


def active_or_latest_version(recipe: Recipe) -> RecipeVersion | None:
    versions = list(recipe.versions.all())
    for version in versions:
        if version.status == RecipeVersionStatus.ACTIVE:
            return version
    return max(versions, key=lambda v: v.version_number) if versions else None


def _product_node(
    product: Product,
    version: RecipeVersion | None,
    image_urls: dict[int, str | None],
    recipe: Recipe | None = None,
) -> dict:
    category = product.category if product.category_id else None
    if category is not None and category.id not in image_urls:
        image_urls[category.id] = category_image_url(category)
    category_url = image_urls.get(category.id) if category is not None else None
    main_url, images = product_photos(product)
    return {
        'product_id': product.id,
        'name': product.name,
        'recipe_code': product.recipe_code,
        'from_location_id': product.source_container_id,
        'from_location_name': (
            product.source_container.name if product.source_container_id else None
        ),
        'to_location_id': product.destination_container_id,
        'to_location_name': (
            product.destination_container.name
            if product.destination_container_id
            else None
        ),
        'category_id': category.id if category is not None else None,
        'category_name': category.name if category is not None else None,
        'category_image_url': category_url,
        'image_url': main_url or category_url,
        'images': images,
        'has_recipe': version is not None,
        'recipe_id': version.recipe_id if version else None,
        'version_id': version.id if version else None,
        'version_number': version.version_number if version else None,
        'version_label': f'v{version.version_number}' if version else None,
        'version_status': version.status if version else None,
        'is_live': bool(
            version and version.status == RecipeVersionStatus.ACTIVE
        ),
        'versions': _version_choices(recipe),
    }


def _version_choices(recipe: Recipe | None) -> list[dict]:
    if recipe is None:
        return []
    return [
        {
            'id': v.id,
            'version_number': v.version_number,
            'version_label': f'v{v.version_number}',
            'status': v.status,
            'can_activate': v.status == RecipeVersionStatus.APPROVED,
        }
        for v in sorted(recipe.versions.all(), key=lambda v: v.version_number)
    ]


def _walk_product(
    product_id: int,
    *,
    path: set[int],
    depth: int,
    nodes_by_id: dict[int, dict],
    edges: list[dict],
    image_urls: dict[int, str | None],
    version_overrides: dict[int, int] | None = None,
) -> dict | None:
    if depth > MAX_TREE_DEPTH or product_id in path:
        return None

    try:
        product = Product.objects.select_related(
            'source_container',
            'destination_container',
            'category',
            'recipe',
        ).prefetch_related(
            'recipe__versions__components__unit',
            'recipe__versions__components__component_product',
        ).get(pk=product_id)
    except Product.DoesNotExist:
        return None

    recipe = None
    try:
        recipe = product.recipe
    except Recipe.DoesNotExist:
        pass
    override_id = (version_overrides or {}).get(product_id)
    version = None
    if override_id is not None:
        versions = list(recipe.versions.all()) if recipe is not None else []
        version = next((v for v in versions if v.id == override_id), None)
        if version is None:
            raise ValueError('That recipe version was not found.')
    elif recipe is not None:
        version = active_or_latest_version(recipe)
    if product_id not in nodes_by_id:
        nodes_by_id[product_id] = _product_node(
            product, version, image_urls, recipe,
        )

    children = []
    if version is None:
        return {**nodes_by_id[product_id], 'children': children}

    next_path = path | {product_id}
    components = sorted(version.components.all(), key=lambda c: c.line_no)
    for component in components:
        child_id = component.component_product_id
        edges.append({
            'parent_product_id': product_id,
            'child_product_id': child_id,
            'quantity': _dec(component.quantity),
            'unit_name': component.unit.name if component.unit_id else None,
            'line_no': component.line_no,
        })
        child_tree = _walk_product(
            child_id,
            path=next_path,
            depth=depth + 1,
            nodes_by_id=nodes_by_id,
            edges=edges,
            image_urls=image_urls,
            version_overrides=version_overrides,
        )
        if child_tree is not None:
            children.append(child_tree)
        elif child_id in nodes_by_id:
            children.append({**nodes_by_id[child_id], 'children': [], 'cycle': True})
        else:
            try:
                child = Product.objects.select_related(
                    'source_container',
                    'destination_container',
                    'category',
                ).get(pk=child_id)
                stub = _product_node(child, None, image_urls)
                nodes_by_id[child_id] = stub
                children.append({**stub, 'children': [], 'cycle': True})
            except Product.DoesNotExist:
                pass

    return {**nodes_by_id[product_id], 'children': children}


def parse_tree_pins(raw: str | None) -> dict[int, int]:
    """Parse `product_id:version_id,product_id:version_id`. Empty → {}."""
    if raw in (None, ''):
        return {}
    pins: dict[int, int] = {}
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            product_s, version_s = part.split(':')
            pins[int(product_s)] = int(version_s)
        except ValueError:
            raise ValueError('pins must be product_id:version_id pairs.') from None
    return pins


def build_recipe_tree(
    product_id: int,
    version_id: int | None = None,
    pins: dict[int, int] | None = None,
) -> dict:
    """
    Recursive recipe dependency tree for a product.
    Uses active version, else latest. Leaves = products with no recipe components.
    version_id pins the root; pins pin nested products. Unpinned nodes stay active.
    """
    nodes_by_id: dict[int, dict] = {}
    edges: list[dict] = []
    image_urls: dict[int, str | None] = {}
    overrides = dict(pins or {})
    if version_id is not None:
        overrides[product_id] = version_id
    tree = _walk_product(
        product_id,
        path=set(),
        depth=0,
        nodes_by_id=nodes_by_id,
        edges=edges,
        image_urls=image_urls,
        version_overrides=overrides,
    )
    if tree is None:
        raise Product.DoesNotExist(f'product_id={product_id} not found.')
    return {
        'root_product_id': product_id,
        'nodes': list(nodes_by_id.values()),
        'edges': edges,
        'tree': tree,
    }
