import os
from functools import wraps

from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt

from locations.views.container_views import container_index_api, global_container_index_api
from locations.views.courier_views import courier_detail_api, courier_list_api
from locations.views.customer_views import customer_detail_api, customer_list_api
from locations.views.department_views import create_department, department_detail_api, department_list_api
from locations.views.location_views import location_detail_api, location_list_api
from locations.views.storage_views import (
    storage_location_detail_api, storage_location_list_api )
 
from locations.views.supplier_views import supplier_detail_api, supplier_list_api

STATIC_CONTAINER_TOKEN = os.getenv('CONTAINER_STATIC_TOKEN', 'dev-static-token')


def auth_middleware(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        api_token = request.headers.get('X-API-Token')
        auth_header = request.headers.get('Authorization', '')
        bearer_token = auth_header[7:].strip() if auth_header.startswith('Bearer ') else None
        token = api_token or bearer_token
        if token != STATIC_CONTAINER_TOKEN:
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
        return view_func(request, *args, **kwargs)

    return _wrapped


container_urlpatterns = [
    path('', auth_middleware(container_index_api), name='container-index'),
    path('locations/', csrf_exempt(auth_middleware(location_list_api)), name='location-list'),
    path(
        'locations/<int:location_id>/',
        csrf_exempt(auth_middleware(location_detail_api)),
        name='location-detail',
    ),
    path('suppliers/', auth_middleware(supplier_list_api), name='supplier-list'),
    path('suppliers/<int:location_id>/', auth_middleware(supplier_detail_api), name='supplier-detail'),
    path('couriers/', auth_middleware(courier_list_api), name='courier-list'),
    path('couriers/<int:location_id>/', auth_middleware(courier_detail_api), name='courier-detail'),
    path('customers/', auth_middleware(customer_list_api), name='customer-list'),
    path('customers/<int:location_id>/', auth_middleware(customer_detail_api), name='customer-detail'),

    path('departments/', department_list_api, name='department-list'),
    path('departments/<int:location_id>/', auth_middleware(department_detail_api), name='department-detail'),
    path('departments/create/', csrf_exempt(auth_middleware(create_department)), name='create-department'),

    path('storage/', auth_middleware(storage_location_list_api), name='storage-location-list'),
    path('storage/<int:location_id>/', auth_middleware(storage_location_detail_api), name='storage-location-detail'),

    path('global-container/', auth_middleware(global_container_index_api), name='global-container-index'),
]

urlpatterns = [
    path('container/', include(container_urlpatterns)),
]
