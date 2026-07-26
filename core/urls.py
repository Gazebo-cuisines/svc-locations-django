"""URL configuration for svc-locations-django — admin disabled."""

from django.urls import include, path

urlpatterns = [
    path('api/auth/', include('users_rbac.urls')),
]
