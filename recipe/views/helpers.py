import json
from datetime import date
from decimal import Decimal, InvalidOperation

from locations.utils.api_response import api_error
from product.audit_log import capture_product_audit
from recipe.attachments import attachment_dict
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus

_RECIPE_AUDIT_ENTITIES = frozenset({
    'recipe', 'recipe_version', 'recipe_component', 'recipe_attachment',
})

_EDITABLE_STATUSES = {
    RecipeVersionStatus.DRAFT,
    RecipeVersionStatus.REJECTED,
}
_VERSION_LOCKED_MSG = 'This version is locked while it is awaiting approval.'


def dec(value):
    return str(value) if value is not None else None


def iso(value):
    return value.isoformat() if value else None


def audit(request, *, product_id, entity, action, before_data, after_data):
    capture_product_audit(
        request,
        product_id=product_id,
        entity=entity,
        action=action,
        before_data=before_data,
        after_data=after_data,
    )


def actor(request):
    user = getattr(request, 'rbac_user', None)
    if not user:
        return None, None
    return user.cognito_sub, (user.display_name or user.username)


def version_locked_response(version: RecipeVersion):
    if version.status in _EDITABLE_STATUSES:
        return None
    return api_error(_VERSION_LOCKED_MSG, status_code=409)


def parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def parse_decimal(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


def parse_date(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.')


def recipe_detail_qs():
    return Recipe.objects.filter(product__is_active=True).select_related(
        'product__source_container',
        'product__destination_container',
    ).prefetch_related(
        'versions__location',
        'versions__components__recipe_version',
        'versions__components__component_product',
        'versions__components__unit',
        'versions__attachments',
        'versions__components__attachments',
    )


def version_detail_qs():
    return RecipeVersion.objects.prefetch_related(
        'components__recipe_version',
        'components__component_product',
        'components__unit',
        'components__attachments',
        'attachments',
    )


def reload_version(pk: int) -> RecipeVersion:
    return version_detail_qs().select_related('recipe').get(pk=pk)


def active_or_latest_version(recipe: Recipe):
    versions = list(recipe.versions.all())
    for version in versions:
        if version.status == RecipeVersionStatus.ACTIVE:
            return version
    return max(versions, key=lambda v: v.version_number) if versions else None


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
        'quantity': dec(component.quantity),
        'unit_id': component.unit_id,
        'unit_name': component.unit.name if component.unit_id else None,
        'batch_quantity': dec(component.batch_quantity),
        'gross_batch_quantity': dec(component.gross_batch_quantity),
        'step_instructions': component.step_instructions,
        'is_implicit': component.is_implicit,
        'attachments': [attachment_dict(a) for a in component.attachments.all()],
    }


def recipe_version_list_dict(version: RecipeVersion) -> dict:
    return {
        'id': version.id,
        'recipe_id': version.recipe_id,
        'version_number': version.version_number,
        'version_label': f'v{version.version_number}',
        'component_count': len(version.components.all()),
        'status': version.status,
        'process_loss': dec(version.process_loss),
        'batch_quantity': dec(version.batch_quantity),
        'batch_unit_id': version.batch_unit_id,
        'location_id': version.location_id,
        'location_name': version.location.name if version.location_id else None,
        'effective_from': (
            version.effective_from.isoformat() if version.effective_from else None
        ),
        'effective_to': (
            version.effective_to.isoformat() if version.effective_to else None
        ),
        'next_review_date': iso(version.next_review_date),
        'activated_at': iso(version.activated_at),
        'approval_reason': version.approval_reason,
        'rejection_reason': version.rejection_reason,
        'created_by_name': version.created_by_name,
        'submitted_by_name': version.submitted_by_name,
        'approved_by_name': version.approved_by_name,
        'rejected_by_name': version.rejected_by_name,
    }


def recipe_version_detail_dict(version: RecipeVersion) -> dict:
    data = recipe_version_list_dict(version)
    data.update({
        'sum_batch_quantity': dec(version.sum_batch_quantity),
        'sum_net_quantity': dec(version.sum_net_quantity),
        'sum_gross_quantity': dec(version.sum_gross_quantity),
        'remarks': version.remarks,
        'submitted_at': iso(version.submitted_at),
        'approved_at': iso(version.approved_at),
        'rejected_at': iso(version.rejected_at),
        'created_at': version.created_at.isoformat() if version.created_at else None,
        'updated_at': version.updated_at.isoformat() if version.updated_at else None,
        'components': [
            component_dict(c)
            for c in version.components.order_by('line_no')
        ],
        'attachments': [
            attachment_dict(a)
            for a in version.attachments.all()
            if a.component_id is None
        ],
    })
    return data


def recipe_list_dict(recipe: Recipe) -> dict:
    version = active_or_latest_version(recipe)
    ingredients = (
        [component_dict(c) for c in sorted(version.components.all(), key=lambda c: c.line_no)]
        if version else []
    )
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
        'batch_quantity': dec(version.batch_quantity) if version else None,
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
