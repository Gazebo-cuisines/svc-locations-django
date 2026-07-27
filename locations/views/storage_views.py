from django.views.decorators.http import require_GET

from locations.models import LocationRole
from locations.presentation import location_detail_dict, location_list_dict
from locations.utils.api_response import api_success
from locations.utils.location_api import (
    apply_list_filters,
    json_location_detail,
    location_queryset,
)


def _storage_queryset():
    return location_queryset().filter(roles__role=LocationRole.STORAGE).distinct()


@require_GET
def storage_location_list_api(request):
    qs = apply_list_filters(request, _storage_queryset())
    results = [location_list_dict(item) for item in qs]
    storage_class = request.GET.get('storage_class')
    if storage_class:
        needle = storage_class.lower()
        results = [row for row in results if needle in (row.get('name') or '').lower()]
    return api_success(
        'Storage location list fetched successfully.',
        data={'count': len(results), 'results': results},
    )


@require_GET
def storage_location_detail_api(request, location_id: int):
    return json_location_detail(
        request,
        location_id,
        _storage_queryset(),
        location_detail_dict,
        message='Storage location fetched successfully.',
        not_found_message='Storage location not found.',
    )
