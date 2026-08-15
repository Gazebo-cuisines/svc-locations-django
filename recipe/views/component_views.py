from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Unit
from product.query import active_products
from recipe.models import RecipeComponent, RecipeVersion
from recipe.permissions import gate_recipe_write
from recipe.utils import RecipeValidationError, assert_not_self_loop, sync_has_recipe
from recipe.views.helpers import (
    audit,
    component_dict,
    parse_decimal,
    parse_json_body,
    version_locked_response,
)


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_write
def recipe_component_collection_api(request, pk: int):
    try:
        version = RecipeVersion.objects.select_related('recipe').get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)
    locked = version_locked_response(version)
    if locked:
        return locked

    body = parse_json_body(request)
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
        quantity = parse_decimal(body['quantity'], 'quantity')
        if quantity is None or quantity <= 0:
            return api_error('quantity must be greater than 0.', status_code=400)

        component = RecipeComponent.objects.create(
            recipe_version=version,
            line_no=body['line_no'],
            component_product_id=component_product_id,
            quantity=quantity,
            unit_id=body['unit_id'],
            batch_quantity=parse_decimal(body.get('batch_quantity'), 'batch_quantity'),
            gross_batch_quantity=parse_decimal(
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

    after_data = component_dict(component)
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_component',
        action='create',
        before_data=None,
        after_data=after_data,
    )
    return api_success(
        'Recipe component created successfully.',
        after_data,
        status_code=201,
    )


@require_http_methods(['PATCH', 'DELETE'])
@csrf_exempt
@gate_recipe_write
def recipe_component_detail_api(request, pk: int):
    try:
        component = RecipeComponent.objects.select_related(
            'recipe_version__recipe',
            'component_product',
            'unit',
        ).get(pk=pk)
    except RecipeComponent.DoesNotExist:
        return api_error('Recipe component not found.', status_code=404)

    locked = version_locked_response(component.recipe_version)
    if locked:
        return locked

    if request.method == 'DELETE':
        product_id = component.recipe_version.recipe.product_id
        before_data = component_dict(component)
        component.delete()
        sync_has_recipe(product_id)
        audit(
            request,
            product_id=product_id,
            entity='recipe_component',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Recipe component deleted successfully.', data=None)

    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    before_data = component_dict(component)
    product_id = component.recipe_version.recipe.product_id
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
            quantity = parse_decimal(body['quantity'], 'quantity')
            if quantity is None or quantity <= 0:
                return api_error('quantity must be greater than 0.', status_code=400)
            component.quantity = quantity

        for field in ('batch_quantity', 'gross_batch_quantity'):
            if field in body:
                setattr(component, field, parse_decimal(body[field], field))

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

    after_data = component_dict(component)
    audit(
        request,
        product_id=product_id,
        entity='recipe_component',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Recipe component updated successfully.',
        after_data,
    )
