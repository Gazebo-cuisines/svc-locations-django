from django.views.decorators.http import require_GET

from locations.utils.api_response import api_success

_CONTAINER_ENDPOINTS = {
    'locations': '/container/locations/',
    'suppliers': '/container/suppliers/',
    'customers': '/container/customers/',
    'departments': '/container/departments/',
    'storage': '/container/storage/',
}


@require_GET
def container_index_api(request):
    return api_success(
        'svc-locations-django container API',
        data={'endpoints': _CONTAINER_ENDPOINTS},
    )
