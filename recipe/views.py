import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location
from locations.utils.api_response import api_error, api_success
from product.models import Product, Unit
from product.query import active_products
from recipe.models import (
    Recipe,
    RecipeComponent,
    RecipeVersion,
    RecipeVersionStatus,
)
from recipe.utils import (
    RecipeValidationError,
    activate_version,
    assert_not_self_loop,
    build_recipe_tree,
    get_or_create_recipe_with_draft,
    next_version_number,
    sync_has_recipe,
)


def _dec(value):
    return str(value) if value is not None else None


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_decimal(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


def _parse_date(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.')


def _active_or_latest_version(recipe: Recipe):
    versions = list(recipe.versions.all())
    for version in versions:
        if version.status == RecipeVersionStatus.ACTIVE:
            return version
    return max(versions, key=lambda v: v.version_number) if versions else None


def _version_ingredients(version: RecipeVersion | None) -> list[dict]:
    if version is None:
        return []
    components = sorted(version.components.all(), key=lambda c: c.line_no)
    return [component_dict(c) for c in components]


def recipe_list_dict(recipe: Recipe) -> dict:
    version = _active_or_latest_version(recipe)
    ingredients = _version_ingredients(version)
    product = recipe.product
    return {
        'id': recipe.id,
        'product_id': recipe.product_id,
        'name': recipe.name,
        'ingredient_count': len(ingredients),
        'ingredients': ingredients,
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
        'location_id': version.location_id if version else None,
        'location_name': (
            version.location.name
            if version and version.location_id
            else None
        ),
        'version_number': version.version_number if version else None,
        'batch_quantity': _dec(version.batch_quantity) if version else None,
    }


def recipe_detail_dict(recipe: Recipe) -> dict:
    data = recipe_list_dict(recipe)
    data.update({
        'remarks': recipe.remarks,
        'created_at': recipe.created_at.isoformat() if recipe.created_at else None,
        'updated_at': recipe.updated_at.isoformat() if recipe.updated_at else None,
        'versions': [
            recipe_version_list_dict(v)
            for v in recipe.versions.order_by('version_number')
        ],
    })
    return data


def recipe_version_list_dict(version: RecipeVersion) -> dict:
    return {
        'id': version.id,
        'recipe_id': version.recipe_id,
        'version_number': version.version_number,
        'version_label': f'v{version.version_number}',
        'component_count': len(version.components.all()),
        'status': version.status,
        'process_loss': _dec(version.process_loss),
        'batch_quantity': _dec(version.batch_quantity),
        'batch_unit_id': version.batch_unit_id,
        'location_id': version.location_id,
        'location_name': version.location.name if version.location_id else None,
        'effective_from': (
            version.effective_from.isoformat() if version.effective_from else None
        ),
        'effective_to': (
            version.effective_to.isoformat() if version.effective_to else None
        ),
    }


def recipe_version_detail_dict(version: RecipeVersion) -> dict:
    data = recipe_version_list_dict(version)
    data.update({
        'sum_batch_quantity': _dec(version.sum_batch_quantity),
        'sum_net_quantity': _dec(version.sum_net_quantity),
        'sum_gross_quantity': _dec(version.sum_gross_quantity),
        'remarks': version.remarks,
        'created_at': version.created_at.isoformat() if version.created_at else None,
        'updated_at': version.updated_at.isoformat() if version.updated_at else None,
        'components': [
            component_dict(c)
            for c in version.components.order_by('line_no')
        ],
    })
    return data


def _recipe_detail_qs():
    return Recipe.objects.filter(product__is_active=True).select_related(
        'product__source_container',
        'product__destination_container',
    ).prefetch_related(
        'versions__location',
        'versions__components__recipe_version',
        'versions__components__component_product',
        'versions__components__unit',
    )


def component_dict(component: RecipeComponent) -> dict:
    return {
        'id': component.id,
        'recipe_id': component.recipe_version.recipe_id,
        'recipe_version_id': component.recipe_version_id,
        'version_number': component.recipe_version.version_number,
        'line_no': component.line_no,
        'component_product_id': component.component_product_id,
        'component_product_name': (
            component.component_product.name
            if component.component_product_id
            else None
        ),
        'quantity': _dec(component.quantity),
        'unit_id': component.unit_id,
        'unit_name': component.unit.name if component.unit_id else None,
        'batch_quantity': _dec(component.batch_quantity),
        'gross_batch_quantity': _dec(component.gross_batch_quantity),
        'step_instructions': component.step_instructions,
        'is_implicit': component.is_implicit,
    }


@require_GET
def recipe_product_tree_api(request, product_id: int):
    if not active_products().filter(pk=product_id).exists():
        return api_error('Product not found.', status_code=404)
    try:
        tree = build_recipe_tree(product_id)
    except Product.DoesNotExist:
        return api_error('Product not found.', status_code=404)
    return api_success('Recipe dependency tree fetched successfully.', tree)


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def recipe_by_product_api(request, product_id: int):
    if not active_products().filter(pk=product_id).exists():
        return api_error('Product not found.', status_code=404)
    recipe, created = get_or_create_recipe_with_draft(product_id)
    recipe = _recipe_detail_qs().get(pk=recipe.pk)
    return api_success(
        'Recipe created successfully.' if created else 'Recipe fetched successfully.',
        recipe_detail_dict(recipe),
        status_code=201 if created else 200,
    )


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def recipe_collection_api(request):
    if request.method == 'GET':
        recipes = (
            Recipe.objects.filter(product__is_active=True)
            .select_related(
                'product__source_container',
                'product__destination_container',
            )
            .prefetch_related(
                'versions__location',
                'versions__components__recipe_version',
                'versions__components__component_product',
                'versions__components__unit',
            )
            .order_by('id')
        )
        return api_success(
            'Recipe list fetched successfully.',
            [recipe_list_dict(r) for r in recipes],
        )
    return recipe_create_api(request)


def recipe_create_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    product_id = body.get('product_id')
    if product_id in (None, ''):
        return api_error('Missing required fields: product_id', status_code=400)

    if not active_products().filter(pk=product_id).exists():
        return api_error(f'product_id={product_id} not found.', status_code=400)

    recipe, created = get_or_create_recipe_with_draft(
        product_id,
        name=body.get('name'),
        remarks=body.get('remarks'),
    )
    recipe = _recipe_detail_qs().get(pk=recipe.pk)
    return api_success(
        'Recipe created successfully.' if created else 'Recipe fetched successfully.',
        recipe_detail_dict(recipe),
        status_code=201 if created else 200,
    )


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def recipe_detail_api(request, pk: int):
    try:
        recipe = _recipe_detail_qs().get(pk=pk)
    except Recipe.DoesNotExist:
        return api_error('Recipe not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Recipe fetched successfully.', recipe_detail_dict(recipe))
    if request.method == 'DELETE':
        return recipe_delete_api(recipe)
    return recipe_update_api(request, recipe)


def recipe_update_api(request, recipe: Recipe):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    if 'name' in body:
        recipe.name = body['name']
    if 'remarks' in body:
        recipe.remarks = body['remarks']

    try:
        recipe.save()
    except IntegrityError as exc:
        return api_error(f'Could not update recipe: {exc}', status_code=400)

    recipe = _recipe_detail_qs().get(pk=recipe.pk)
    return api_success('Recipe updated successfully.', recipe_detail_dict(recipe))


def recipe_delete_api(recipe: Recipe):
    product_id = recipe.product_id
    # versions FK is PROTECT; components CASCADE from versions
    with transaction.atomic():
        version_ids = list(recipe.versions.values_list('pk', flat=True))
        RecipeComponent.objects.filter(recipe_version_id__in=version_ids).delete()
        recipe.versions.all().delete()
        recipe.delete()
    sync_has_recipe(product_id)
    return api_success('Recipe deleted successfully.', data=None)


@require_http_methods(['POST'])
@csrf_exempt
def recipe_version_collection_api(request, pk: int):
    try:
        recipe = Recipe.objects.get(pk=pk)
    except Recipe.DoesNotExist:
        return api_error('Recipe not found.', status_code=404)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    status = body.get('status', RecipeVersionStatus.DRAFT)
    if status == RecipeVersionStatus.ACTIVE:
        return api_error(
            'Use the activate endpoint to make a version active.',
            status_code=400,
        )
    if status not in RecipeVersionStatus.values:
        return api_error('Invalid status.', status_code=400)

    version_number = next_version_number(recipe)

    batch_unit_id = body.get('batch_unit_id')
    if batch_unit_id is not None and not Unit.objects.filter(pk=batch_unit_id).exists():
        return api_error(f'batch_unit_id={batch_unit_id} not found.', status_code=400)

    location_id = body.get('location_id')
    if location_id is not None and not Location.objects.filter(pk=location_id).exists():
        return api_error(f'location_id={location_id} not found.', status_code=400)

    try:
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=version_number,
            status=status,
            process_loss=_parse_decimal(
                body.get('process_loss', '1.0000'), 'process_loss',
            ) or Decimal('1.0000'),
            batch_quantity=_parse_decimal(body.get('batch_quantity'), 'batch_quantity'),
            batch_unit_id=batch_unit_id,
            sum_batch_quantity=_parse_decimal(
                body.get('sum_batch_quantity'), 'sum_batch_quantity',
            ),
            sum_net_quantity=_parse_decimal(
                body.get('sum_net_quantity'), 'sum_net_quantity',
            ),
            sum_gross_quantity=_parse_decimal(
                body.get('sum_gross_quantity'), 'sum_gross_quantity',
            ),
            location_id=location_id,
            effective_from=_parse_date(body.get('effective_from'), 'effective_from'),
            effective_to=_parse_date(body.get('effective_to'), 'effective_to'),
            remarks=body.get('remarks'),
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create recipe version: {exc}', status_code=400)

    return api_success(
        'Recipe version created successfully.',
        recipe_version_detail_dict(version),
        status_code=201,
    )


@require_http_methods(['GET', 'PATCH'])
@csrf_exempt
def recipe_version_detail_api(request, pk: int):
    try:
        version = RecipeVersion.objects.prefetch_related(
            'components__recipe_version',
            'components__component_product',
            'components__unit',
        ).get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    if request.method == 'GET':
        return api_success(
            'Recipe version fetched successfully.',
            recipe_version_detail_dict(version),
        )
    return recipe_version_update_api(request, version)


def recipe_version_update_api(request, version: RecipeVersion):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        if 'status' in body:
            status = body['status']
            if status == RecipeVersionStatus.ACTIVE:
                return api_error(
                    'Use the activate endpoint to make a version active.',
                    status_code=400,
                )
            if status not in RecipeVersionStatus.values:
                return api_error('Invalid status.', status_code=400)
            version.status = status

        if 'process_loss' in body:
            value = _parse_decimal(body['process_loss'], 'process_loss')
            if value is None or value <= 0:
                return api_error('process_loss must be greater than 0.', status_code=400)
            version.process_loss = value

        for field in (
            'batch_quantity',
            'sum_batch_quantity',
            'sum_net_quantity',
            'sum_gross_quantity',
        ):
            if field in body:
                setattr(version, field, _parse_decimal(body[field], field))

        if 'batch_unit_id' in body:
            batch_unit_id = body['batch_unit_id']
            if (
                batch_unit_id is not None
                and not Unit.objects.filter(pk=batch_unit_id).exists()
            ):
                return api_error(
                    f'batch_unit_id={batch_unit_id} not found.',
                    status_code=400,
                )
            version.batch_unit_id = batch_unit_id

        if 'location_id' in body:
            location_id = body['location_id']
            if (
                location_id is not None
                and not Location.objects.filter(pk=location_id).exists()
            ):
                return api_error(
                    f'location_id={location_id} not found.',
                    status_code=400,
                )
            version.location_id = location_id

        if 'effective_from' in body:
            version.effective_from = _parse_date(
                body['effective_from'], 'effective_from',
            )
        if 'effective_to' in body:
            version.effective_to = _parse_date(body['effective_to'], 'effective_to')
        if 'remarks' in body:
            version.remarks = body['remarks']

        version.save()
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not update recipe version: {exc}', status_code=400)

    version = RecipeVersion.objects.prefetch_related(
        'components__recipe_version',
        'components__component_product',
        'components__unit',
    ).get(pk=version.pk)
    return api_success(
        'Recipe version updated successfully.',
        recipe_version_detail_dict(version),
    )


@require_http_methods(['POST'])
@csrf_exempt
def recipe_version_activate_api(request, pk: int):
    try:
        version = RecipeVersion.objects.get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    version = activate_version(version)
    version = RecipeVersion.objects.prefetch_related(
        'components__recipe_version',
        'components__component_product',
        'components__unit',
    ).get(pk=version.pk)
    return api_success(
        'Recipe version activated successfully.',
        recipe_version_detail_dict(version),
    )


@require_http_methods(['POST'])
@csrf_exempt
def recipe_component_collection_api(request, pk: int):
    try:
        version = RecipeVersion.objects.select_related('recipe').get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    required = ['line_no', 'component_product_id', 'quantity', 'unit_id']
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(
            f'Missing required fields: {", ".join(missing)}',
            status_code=400,
        )

    component_product_id = body['component_product_id']
    if not active_products().filter(pk=component_product_id).exists():
        return api_error(
            f'component_product_id={component_product_id} not found.',
            status_code=400,
        )
    if not Unit.objects.filter(pk=body['unit_id']).exists():
        return api_error(f'unit_id={body["unit_id"]} not found.', status_code=400)

    try:
        assert_not_self_loop(
            parent_product_id=version.recipe.product_id,
            component_product_id=component_product_id,
        )
        quantity = _parse_decimal(body['quantity'], 'quantity')
        if quantity is None or quantity <= 0:
            return api_error('quantity must be greater than 0.', status_code=400)

        component = RecipeComponent.objects.create(
            recipe_version=version,
            line_no=body['line_no'],
            component_product_id=component_product_id,
            quantity=quantity,
            unit_id=body['unit_id'],
            batch_quantity=_parse_decimal(body.get('batch_quantity'), 'batch_quantity'),
            gross_batch_quantity=_parse_decimal(
                body.get('gross_batch_quantity'), 'gross_batch_quantity',
            ),
            step_instructions=body.get('step_instructions'),
            is_implicit=bool(body.get('is_implicit', False)),
        )
        sync_has_recipe(version.recipe.product_id)
        component = RecipeComponent.objects.select_related(
            'recipe_version',
            'component_product',
            'unit',
        ).get(pk=component.pk)
    except RecipeValidationError as exc:
        return api_error(str(exc), status_code=400)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create component: {exc}', status_code=400)

    return api_success(
        'Recipe component created successfully.',
        component_dict(component),
        status_code=201,
    )


@require_http_methods(['PATCH', 'DELETE'])
@csrf_exempt
def recipe_component_detail_api(request, pk: int):
    try:
        component = RecipeComponent.objects.select_related(
            'recipe_version__recipe',
            'component_product',
            'unit',
        ).get(pk=pk)
    except RecipeComponent.DoesNotExist:
        return api_error('Recipe component not found.', status_code=404)

    if request.method == 'DELETE':
        product_id = component.recipe_version.recipe.product_id
        component.delete()
        sync_has_recipe(product_id)
        return api_success('Recipe component deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        if 'component_product_id' in body:
            component_product_id = body['component_product_id']
            if not active_products().filter(pk=component_product_id).exists():
                return api_error(
                    f'component_product_id={component_product_id} not found.',
                    status_code=400,
                )
            assert_not_self_loop(
                parent_product_id=component.recipe_version.recipe.product_id,
                component_product_id=component_product_id,
            )
            component.component_product_id = component_product_id

        if 'unit_id' in body:
            if not Unit.objects.filter(pk=body['unit_id']).exists():
                return api_error(
                    f'unit_id={body["unit_id"]} not found.',
                    status_code=400,
                )
            component.unit_id = body['unit_id']

        if 'line_no' in body:
            component.line_no = body['line_no']

        if 'quantity' in body:
            quantity = _parse_decimal(body['quantity'], 'quantity')
            if quantity is None or quantity <= 0:
                return api_error('quantity must be greater than 0.', status_code=400)
            component.quantity = quantity

        for field in ('batch_quantity', 'gross_batch_quantity'):
            if field in body:
                setattr(component, field, _parse_decimal(body[field], field))

        if 'step_instructions' in body:
            component.step_instructions = body['step_instructions']
        if 'is_implicit' in body:
            component.is_implicit = bool(body['is_implicit'])

        component.save()
        component = RecipeComponent.objects.select_related(
            'recipe_version',
            'component_product',
            'unit',
        ).get(pk=component.pk)
    except RecipeValidationError as exc:
        return api_error(str(exc), status_code=400)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not update component: {exc}', status_code=400)

    return api_success(
        'Recipe component updated successfully.',
        component_dict(component),
    )
