from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Product
from product.query import active_products
from recipe.models import Recipe, RecipeComponent
from recipe.permissions import gate_recipe_write
from recipe.utils import build_recipe_tree, get_or_create_recipe_with_draft, sync_has_recipe
from recipe.views.helpers import (
    actor,
    audit,
    parse_json_body,
    recipe_detail_dict,
    recipe_detail_qs,
    recipe_list_dict,
)


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
@gate_recipe_write
def recipe_by_product_api(request, product_id: int):
    if not active_products().filter(pk=product_id).exists():
        return api_error('Product not found.', status_code=404)
    sub, name = actor(request)
    recipe, created = get_or_create_recipe_with_draft(
        product_id,
        created_by_sub=sub,
        created_by_name=name,
    )
    recipe = recipe_detail_qs().get(pk=recipe.pk)
    after = recipe_detail_dict(recipe)
    if created:
        audit(
            request,
            product_id=product_id,
            entity='recipe',
            action='create',
            before_data=None,
            after_data=after,
        )
    return api_success(
        'Recipe created successfully.' if created else 'Recipe fetched successfully.',
        after,
        status_code=201 if created else 200,
    )


@require_http_methods(['GET', 'POST'])
@csrf_exempt
@gate_recipe_write
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
    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    product_id = body.get('product_id')
    if product_id in (None, ''):
        return api_error('Missing required fields: product_id', status_code=400)

    if not active_products().filter(pk=product_id).exists():
        return api_error(f'product_id={product_id} not found.', status_code=400)

    sub, name = actor(request)
    recipe, created = get_or_create_recipe_with_draft(
        product_id,
        name=body.get('name'),
        remarks=body.get('remarks'),
        created_by_sub=sub,
        created_by_name=name,
    )
    recipe = recipe_detail_qs().get(pk=recipe.pk)
    after = recipe_detail_dict(recipe)
    if created:
        audit(
            request,
            product_id=product_id,
            entity='recipe',
            action='create',
            before_data=None,
            after_data=after,
        )
    return api_success(
        'Recipe created successfully.' if created else 'Recipe fetched successfully.',
        after,
        status_code=201 if created else 200,
    )


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
@gate_recipe_write
def recipe_detail_api(request, pk: int):
    try:
        recipe = recipe_detail_qs().get(pk=pk)
    except Recipe.DoesNotExist:
        return api_error('Recipe not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Recipe fetched successfully.', recipe_detail_dict(recipe))
    if request.method == 'DELETE':
        return recipe_delete_api(request, recipe)
    return recipe_update_api(request, recipe)


def recipe_update_api(request, recipe: Recipe):
    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    before_data = recipe_detail_dict(recipe)
    if 'name' in body:
        recipe.name = body['name']
    if 'remarks' in body:
        recipe.remarks = body['remarks']

    try:
        recipe.save()
    except IntegrityError as exc:
        return api_error(f'Could not update recipe: {exc}', status_code=400)

    recipe = recipe_detail_qs().get(pk=recipe.pk)
    after_data = recipe_detail_dict(recipe)
    audit(
        request,
        product_id=recipe.product_id,
        entity='recipe',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success('Recipe updated successfully.', after_data)


def recipe_delete_api(request, recipe: Recipe):
    product_id = recipe.product_id
    before_data = recipe_detail_dict(recipe)
    with transaction.atomic():
        version_ids = list(recipe.versions.values_list('pk', flat=True))
        RecipeComponent.objects.filter(recipe_version_id__in=version_ids).delete()
        recipe.versions.all().delete()
        recipe.delete()
    sync_has_recipe(product_id)
    audit(
        request,
        product_id=product_id,
        entity='recipe',
        action='delete',
        before_data=before_data,
        after_data=None,
    )
    return api_success('Recipe deleted successfully.', data=None)
