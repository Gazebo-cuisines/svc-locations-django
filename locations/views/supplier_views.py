from django.views.decorators.http import require_GET

from locations.models import LocationRole
from locations.presentation import location_detail_dict, location_list_dict
from locations.utils.location_api import (
    json_location_detail,
    json_location_list,
    location_queryset,
)


def _supplier_queryset():
    return location_queryset().filter(roles__role=LocationRole.SUPPLIER).distinct()


@require_GET
def supplier_list_api(request):
    return json_location_list(
        request,
        _supplier_queryset(),
        location_list_dict,
        message='Supplier list fetched successfully.',
    )


@require_GET
def supplier_detail_api(request, location_id: int):
    return json_location_detail(
        request,
        location_id,
        _supplier_queryset(),
        location_detail_dict,
        message='Supplier fetched successfully.',
        not_found_message='Supplier not found.',
    )
