"""URL configuration for svc-locations-django — admin disabled."""

from django.urls import include, path

urlpatterns = [
    path('', include('locations.urls')),
]
