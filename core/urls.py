"""URL configuration for svc-locations-django — admin disabled."""

from django.urls import include, path

from core.app_version import app_version
from core.ops_views import error_detail, errors_collection
from core.search import global_search_api

urlpatterns = [
    path('app/version/', app_version, name='app-version'),
    path('search/', global_search_api, name='global-search'),
    path('ops/errors/', errors_collection, name='ops-errors'),
    path('ops/errors/<int:pk>/', error_detail, name='ops-error-detail'),
    path('', include('locations.urls')),
    path('product/', include('product.urls')),
    path('recipe/', include('recipe.urls')),
    path('stock/', include('stock_ledger.urls')),
    path('planning/', include('planning.urls')),
    path('purchasing/', include('purchasing.urls')),
    path('auth/', include('users_rbac.urls')),
    path('hardware/', include('hardware.urls')),
]
