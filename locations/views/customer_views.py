from django.views.decorators.http import require_GET

from locations.models import LocationRole
from locations.presentation import location_detail_dict, location_list_dict
from locations.utils.location_api import (
    json_location_detail,
    json_location_list,
    location_queryset,
)


def _customer_queryset():
    return location_queryset().filter(roles__role=LocationRole.CUSTOMER).distinct()


@require_GET
def customer_list_api(request):
    return json_location_list(
        request,
        _customer_queryset(),
        location_list_dict,
        message='Customer list fetched successfully.',
    )


@require_GET
def customer_detail_api(request, location_id: int):
    return json_location_detail(
        request,
        location_id,
        _customer_queryset(),
        location_detail_dict,
        message='Customer fetched successfully.',
        not_found_message='Customer not found.',
    )
