from django.urls import path

from recipe.views import (
    recipe_collection_api,
    recipe_component_collection_api,
    recipe_component_detail_api,
    recipe_detail_api,
    recipe_version_activate_api,
    recipe_version_collection_api,
    recipe_version_detail_api,
)

urlpatterns = [
    # Recipe endpoints
    path('', recipe_collection_api, name='recipe-collection'),
    path('<int:pk>/', recipe_detail_api, name='recipe-detail'),
    
    # Recipe version endpoints
    path('<int:pk>/versions/', recipe_version_collection_api, name='recipe-version-collection'),
    path('versions/<int:pk>/', recipe_version_detail_api, name='recipe-version-detail'),
    path('versions/<int:pk>/activate/', recipe_version_activate_api, name='recipe-version-activate'),
    
    # Recipe component endpoints
    path('versions/<int:pk>/components/', recipe_component_collection_api, name='recipe-component-collection'),
    path('components/<int:pk>/', recipe_component_detail_api, name='recipe-component-detail'),
]
