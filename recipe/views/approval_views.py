from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from recipe.models import RecipeVersion, RecipeVersionStatus
from recipe.permissions import gate_recipe_activate, gate_recipe_write
from recipe.utils import activate_version
from recipe.views.helpers import (
    _EDITABLE_STATUSES,
    _VERSION_LOCKED_MSG,
    actor,
    audit,
    iso,
    parse_date,
    parse_json_body,
    recipe_version_detail_dict,
    reload_version,
)


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_write
def recipe_version_submit_api(request, pk: int):
    try:
        version = reload_version(pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)
    if version.status not in _EDITABLE_STATUSES:
        return api_error(_VERSION_LOCKED_MSG, status_code=409)
    if not version.components.exists():
        return api_error(
            'Add at least one ingredient before submitting.',
            status_code=400,
        )
    before_data = recipe_version_detail_dict(version)
    sub, name = actor(request)
    version.status = RecipeVersionStatus.PENDING_APPROVAL
    version.submitted_by_sub = sub
    version.submitted_by_name = name
    version.submitted_at = timezone.now()
    version.save(update_fields=[
        'status', 'submitted_by_sub', 'submitted_by_name', 'submitted_at',
        'updated_at',
    ])
    after_data = recipe_version_detail_dict(reload_version(version.pk))
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_version',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success('Recipe version submitted for approval.', after_data)


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_activate
def recipe_version_approve_api(request, pk: int):
    try:
        version = reload_version(pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)
    if version.status != RecipeVersionStatus.PENDING_APPROVAL:
        return api_error(
            'Only a version awaiting approval can be approved.',
            status_code=409,
        )
    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    reason = body.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        return api_error('Reason is required.', status_code=400)
    reason = reason.strip()
    try:
        effective_from = parse_date(body.get('effective_from'), 'effective_from')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    if effective_from is None:
        return api_error('effective_from is required.', status_code=400)
    try:
        next_review_date = parse_date(body.get('next_review_date'), 'next_review_date')
        effective_to = parse_date(body.get('effective_to'), 'effective_to')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    before_data = recipe_version_detail_dict(version)
    sub, name = actor(request)
    version.approved_by_sub = sub
    version.approved_by_name = name
    version.approved_at = timezone.now()
    version.approval_reason = reason
    version.effective_from = effective_from
    version.next_review_date = next_review_date
    version.effective_to = effective_to
    version.status = RecipeVersionStatus.APPROVED
    version.save()
    if effective_from <= timezone.localdate():
        version = activate_version(version)
    after_data = recipe_version_detail_dict(reload_version(version.pk))
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_version',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success('Recipe version approved.', after_data)


@require_http_methods(['POST'])
@csrf_exempt
@gate_recipe_activate
def recipe_version_reject_api(request, pk: int):
    try:
        version = reload_version(pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)
    if version.status != RecipeVersionStatus.PENDING_APPROVAL:
        return api_error(
            'Only a version awaiting approval can be rejected.',
            status_code=409,
        )
    body = parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    reason = body.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        return api_error('Reason is required.', status_code=400)
    reason = reason.strip()
    before_data = recipe_version_detail_dict(version)
    sub, name = actor(request)
    version.status = RecipeVersionStatus.REJECTED
    version.rejected_by_sub = sub
    version.rejected_by_name = name
    version.rejected_at = timezone.now()
    version.rejection_reason = reason
    version.save(update_fields=[
        'status', 'rejected_by_sub', 'rejected_by_name', 'rejected_at',
        'rejection_reason', 'updated_at',
    ])
    after_data = recipe_version_detail_dict(reload_version(version.pk))
    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_version',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success('Recipe version rejected.', after_data)


@require_GET
def recipe_version_history_api(request, pk: int):
    try:
        version = RecipeVersion.objects.select_related('recipe').get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)
    return api_success(
        'Recipe version history fetched successfully.',
        {
            'id': version.id,
            'recipe_id': version.recipe_id,
            'version_number': version.version_number,
            'status': version.status,
            'created_by_sub': version.created_by_sub,
            'created_by_name': version.created_by_name,
            'created_at': iso(version.created_at),
            'submitted_by_sub': version.submitted_by_sub,
            'submitted_by_name': version.submitted_by_name,
            'submitted_at': iso(version.submitted_at),
            'approved_by_sub': version.approved_by_sub,
            'approved_by_name': version.approved_by_name,
            'approved_at': iso(version.approved_at),
            'approval_reason': version.approval_reason,
            'rejected_by_sub': version.rejected_by_sub,
            'rejected_by_name': version.rejected_by_name,
            'rejected_at': iso(version.rejected_at),
            'rejection_reason': version.rejection_reason,
            'effective_from': iso(version.effective_from),
            'effective_to': iso(version.effective_to),
            'next_review_date': iso(version.next_review_date),
            'activated_at': iso(version.activated_at),
        },
    )
