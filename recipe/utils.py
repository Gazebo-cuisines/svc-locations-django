from django.db import transaction
from django.utils import timezone

from product.category_images import category_image_url
from product.models import Product, ProductFlags
from product.product_images import product_photos
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus

MAX_TREE_DEPTH = 20


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
    locked = RecipeVersion.objects.select_for_update().filter(
        recipe_id=version.recipe_id,
    )
    target = locked.get(pk=version.pk)
    locked.filter(status=RecipeVersionStatus.ACTIVE).exclude(pk=target.pk).update(
        status=RecipeVersionStatus.RETIRED,
    )
    if target.status != RecipeVersionStatus.ACTIVE:
        target.status = RecipeVersionStatus.ACTIVE
        target.activated_at = timezone.now()
        target.save(update_fields=['status', 'activated_at', 'updated_at'])
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
    }


def _walk_product(
    product_id: int,
    *,
    path: set[int],
    depth: int,
    nodes_by_id: dict[int, dict],
    edges: list[dict],
    image_urls: dict[int, str | None],
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
    version = active_or_latest_version(recipe) if recipe is not None else None
    if product_id not in nodes_by_id:
        nodes_by_id[product_id] = _product_node(product, version, image_urls)

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


def build_recipe_tree(product_id: int) -> dict:
    """
    Recursive recipe dependency tree for a product.
    Uses active version, else latest. Leaves = products with no recipe components.
    """
    nodes_by_id: dict[int, dict] = {}
    edges: list[dict] = []
    image_urls: dict[int, str | None] = {}
    tree = _walk_product(
        product_id,
        path=set(),
        depth=0,
        nodes_by_id=nodes_by_id,
        edges=edges,
        image_urls=image_urls,
    )
    if tree is None:
        raise Product.DoesNotExist(f'product_id={product_id} not found.')
    return {
        'root_product_id': product_id,
        'nodes': list(nodes_by_id.values()),
        'edges': edges,
        'tree': tree,
    }
