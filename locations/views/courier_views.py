from django.views.decorators.http import require_GET

from locations.models import LocationRole
from locations.presentation import location_detail_dict, location_list_dict
from locations.utils.location_api import (
    json_location_detail,
    json_location_list,
    location_queryset,
)


def _courier_queryset():
    return location_queryset().filter(roles__role=LocationRole.COURIER).distinct()


@require_GET
def courier_list_api(request):
    return json_location_list(
        request,
        _courier_queryset(),
        location_list_dict,
        message='Courier list fetched successfully.',
    )


@require_GET
def courier_detail_api(request, location_id: int):
    return json_location_detail(
        request,
        location_id,
        _courier_queryset(),
        location_detail_dict,
        message='Courier fetched successfully.',
        not_found_message='Courier not found.',
    )
