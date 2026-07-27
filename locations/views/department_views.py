from django.views.decorators.http import require_GET

from locations.models import LocationRole
from locations.presentation import location_detail_dict, location_list_dict
from locations.utils.api_response import api_success
from locations.utils.location_api import (
    apply_list_filters,
    json_location_detail,
    location_queryset,
)


def _department_base_queryset():
    return location_queryset().filter(
        roles__role__in=[
            LocationRole.INTERNAL,
            LocationRole.PROCESS,
            LocationRole.TRANSFORM,
        ],
    ).distinct()


@require_GET
def department_list_api(request):
    qs = _department_base_queryset()
    department_only = request.GET.get('department_only')
    if department_only and department_only.lower() in ('1', 'true', 'yes'):
        qs = qs.filter(roles__role=LocationRole.PROCESS).distinct()
    qs = apply_list_filters(request, qs)
    results = [location_list_dict(item) for item in qs]
    return api_success(
        'Department list fetched successfully.',
        data={'count': len(results), 'results': results},
    )


@require_GET
def department_detail_api(request, location_id: int):
    return json_location_detail(
        request,
        location_id,
        _department_base_queryset(),
        location_detail_dict,
        message='Department fetched successfully.',
        not_found_message='Department not found.',
    )
