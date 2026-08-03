"""URL configuration for svc-locations-django — admin disabled."""

from django.urls import include, path


urlpatterns = [
    path('', include('locations.urls')),
    path('product/', include('product.urls')),
    path('recipe/', include('recipe.urls')),
    path('stock/', include('stock_ledger.urls')),
    path('planning/', include('planning.urls')),
    path('production/', include('production_register.urls')),
    path('auth/', include('users_rbac.urls')),
]
