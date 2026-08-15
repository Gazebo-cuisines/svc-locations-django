from django.views.decorators.http import require_GET

from locations.utils.api_response import api_error, api_success
from product.models import ProductAudit
from recipe.models import Recipe
from recipe.views.helpers import _RECIPE_AUDIT_ENTITIES


def _event_version_id(event: dict):
    for payload in (event.get('after_json'), event.get('before_json')):
        if not isinstance(payload, dict):
            continue
        if event.get('entity') == 'recipe_version' and payload.get('id') is not None:
            return payload.get('id')
        if payload.get('recipe_version_id') is not None:
            return payload.get('recipe_version_id')
    return None


@require_GET
def recipe_audit_api(request, pk: int):
    try:
        recipe = Recipe.objects.get(pk=pk)
    except Recipe.DoesNotExist:
        return api_error('Recipe not found.', status_code=404)

    row = ProductAudit.objects.filter(product_id=recipe.product_id).first()
    events = []
    if row and isinstance(row.timeline_events, list):
        events = [
            event
            for event in reversed(row.timeline_events)
            if event.get('entity') in _RECIPE_AUDIT_ENTITIES
        ]
    version_id = request.GET.get('version_id')
    if version_id not in (None, ''):
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            return api_error('version_id must be an integer.', status_code=400)
        events = [
            event for event in events if _event_version_id(event) == version_id
        ]
    return api_success('Recipe audit fetched successfully.', events)
