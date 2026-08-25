from decimal import Decimal

from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.models import Location
from locations.utils.api_response import api_error, api_success
from product.models import Unit
from recipe.models import Recipe, RecipeVersion, RecipeVersionStatus
from recipe.permissions import gate_recipe_activate, gate_recipe_write
from recipe.utils import activate_version, clone_version, next_version_number
from recipe.views.helpers import (
    actor,
    audit,
    factor_from_body,
    parse_date,
    parse_decimal,
    parse_json_body,
    recipe_version_detail_dict,
    reload_version,
    version_detail_qs,
    version_locked_response,
)


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_write
def recipe_version_collection_api(request, pk: int):
    try:
        recipe = Recipe.objects.get(pk=pk)
    except Recipe.DoesNotExist:
        return api_error('Recipe not found.', status_code=404)

    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    sub, name = actor(request)
    source = None
    copy_from_version_id = body.get('copy_from_version_id')
    if copy_from_version_id not in (None, ''):
        try:
            source = RecipeVersion.objects.prefetch_related('components').get(
                pk=copy_from_version_id,
            )
        except RecipeVersion.DoesNotExist:
            return api_error('Recipe version not found.', status_code=404)
        if source.recipe_id != recipe.id:
            return api_error(
                'That version does not belong to this recipe.',
                status_code=400,
            )

    batch_unit_id = body.get('batch_unit_id')
    if batch_unit_id is not None and not Unit.objects.filter(pk=batch_unit_id).exists():
        return api_error(f'batch_unit_id={batch_unit_id} not found.', status_code=400)

    location_id = body.get('location_id')
    if location_id is not None and not Location.objects.filter(pk=location_id).exists():
        return api_error(f'location_id={location_id} not found.', status_code=400)

    try:
        with transaction.atomic():
            if source is not None:
                version = clone_version(
                    source,
                    created_by_sub=sub,
                    created_by_name=name,
                )
                factor = factor_from_body(body)
                if factor is not None:
                    version.process_loss = factor
                if 'batch_quantity' in body:
                    version.batch_quantity = parse_decimal(
                        body['batch_quantity'], 'batch_quantity',
                    )
                if 'batch_unit_id' in body:
                    version.batch_unit_id = batch_unit_id
                if 'sum_batch_quantity' in body:
                    version.sum_batch_quantity = parse_decimal(
                        body['sum_batch_quantity'], 'sum_batch_quantity',
                    )
                if 'sum_net_quantity' in body:
                    version.sum_net_quantity = parse_decimal(
                        body['sum_net_quantity'], 'sum_net_quantity',
                    )
                if 'sum_gross_quantity' in body:
                    version.sum_gross_quantity = parse_decimal(
                        body['sum_gross_quantity'], 'sum_gross_quantity',
                    )
                if 'location_id' in body:
                    version.location_id = location_id
                if 'effective_from' in body:
                    version.effective_from = parse_date(
                        body['effective_from'], 'effective_from',
                    )
                if 'effective_to' in body:
                    version.effective_to = parse_date(
                        body['effective_to'], 'effective_to',
                    )
                if 'remarks' in body:
                    version.remarks = body['remarks']
                version.save()
            else:
                version = RecipeVersion.objects.create(
                    recipe=recipe,
                    version_number=next_version_number(recipe),
                    status=RecipeVersionStatus.DRAFT,
                    created_by_sub=sub,
                    created_by_name=name,
                    process_loss=factor_from_body(body) or Decimal('1.0000'),
                    batch_quantity=parse_decimal(
                        body.get('batch_quantity'), 'batch_quantity',
                    ),
                    batch_unit_id=batch_unit_id,
                    sum_batch_quantity=parse_decimal(
                        body.get('sum_batch_quantity'), 'sum_batch_quantity',
                    ),
                    sum_net_quantity=parse_decimal(
                        body.get('sum_net_quantity'), 'sum_net_quantity',
                    ),
                    sum_gross_quantity=parse_decimal(
                        body.get('sum_gross_quantity'), 'sum_gross_quantity',
                    ),
                    location_id=location_id,
                    effective_from=parse_date(
                        body.get('effective_from'), 'effective_from',
                    ),
                    effective_to=parse_date(body.get('effective_to'), 'effective_to'),
                    remarks=body.get('remarks'),
                )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create recipe version: {exc}', status_code=400)

    version = version_detail_qs().get(pk=version.pk)
    data = recipe_version_detail_dict(version)
    if source is not None:
        data['copied_from_version_id'] = source.id
    audit(
        request,
        product_id=recipe.product_id,
        entity='recipe_version',
        action='create',
        before_data=None,
        after_data=data,
    )
    return api_success(
        'Recipe version created successfully.',
        data,
        status_code=201,
    )


@require_http_methods(['GET', 'PATCH'])
@csrf_exempt
@gate_recipe_write
def recipe_version_detail_api(request, pk: int):
    try:
        version = version_detail_qs().get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    if request.method == 'GET':
        return api_success(
            'Recipe version fetched successfully.',
            recipe_version_detail_dict(version),
        )
    return recipe_version_update_api(request, version)


def recipe_version_update_api(request, version: RecipeVersion):
    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    if 'status' in body:
        return api_error(
            'Use submit, approve, or reject to change status.',
            status_code=400,
        )
    locked = version_locked_response(version)
    if locked:
        return locked

    before_data = recipe_version_detail_dict(version)
    try:
        factor = factor_from_body(body)
        if factor is not None:
            version.process_loss = factor

        for field in (
            'batch_quantity',
            'sum_batch_quantity',
            'sum_net_quantity',
            'sum_gross_quantity',
        ):
            if field in body:
                setattr(version, field, parse_decimal(body[field], field))

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
            version.effective_from = parse_date(
                body['effective_from'], 'effective_from',
            )
        if 'effective_to' in body:
            version.effective_to = parse_date(body['effective_to'], 'effective_to')
        if 'remarks' in body:
            version.remarks = body['remarks']

        version.save()
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not update recipe version: {exc}', status_code=400)

    version = reload_version(version.pk)
    after_data = recipe_version_detail_dict(version)
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_version',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Recipe version updated successfully.',
        after_data,
    )


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_activate
def recipe_version_activate_api(request, pk: int):
    try:
        version = reload_version(pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    if version.status != RecipeVersionStatus.APPROVED:
        return api_error(
            'Only an approved version can be activated.',
            status_code=409,
        )

    before_data = recipe_version_detail_dict(version)
    version = activate_version(version)
    version = reload_version(version.pk)
    after_data = recipe_version_detail_dict(version)
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_version',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Recipe version activated successfully.',
        after_data,
    )
