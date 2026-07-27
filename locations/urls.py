from django.urls import include, path

from locations.views.container_views import container_index_api
from locations.views.customer_views import customer_detail_api, customer_list_api
from locations.views.department_views import department_detail_api, department_list_api
from locations.views.location_views import location_detail_api, location_list_api
from locations.views.storage_views import (
    storage_location_detail_api,
    storage_location_list_api,
)
from locations.views.supplier_views import supplier_detail_api, supplier_list_api

container_urlpatterns = [
    path('', container_index_api, name='container-index'),
    path('locations/', location_list_api, name='location-list'),
    path('locations/<int:location_id>/', location_detail_api, name='location-detail'),
    path('suppliers/', supplier_list_api, name='supplier-list'),
    path('suppliers/<int:location_id>/', supplier_detail_api, name='supplier-detail'),
    path('customers/', customer_list_api, name='customer-list'),
    path('customers/<int:location_id>/', customer_detail_api, name='customer-detail'),
    path('departments/', department_list_api, name='department-list'),
    path('departments/<int:location_id>/', department_detail_api, name='department-detail'),
    path('storage/', storage_location_list_api, name='storage-location-list'),
    path('storage/<int:location_id>/', storage_location_detail_api, name='storage-location-detail'),
]

urlpatterns = [
    path('container/', include(container_urlpatterns)),
]
