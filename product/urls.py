from django.urls import path

from product.views.acceptance_views import product_acceptance_api
from product.views.allergen_views import (
    product_allergen_detail_api,
    product_allergens_api,
)
from product.views.flags_views import product_flags_api
from product.views.ingredient_label_views import product_ingredient_label_api
from product.views.nutrition_views import product_nutrition_api
from product.views.product_master_view import product_collection_api, product_detail_api
from product.views.technical_views import product_technical_api
from product.views.yield_views import product_yield_api

urlpatterns = [
    path('', product_collection_api, name='product-collection'),
    path('<int:pk>/', product_detail_api, name='product-detail'),
    path('<int:pk>/flags/', product_flags_api, name='product-flags'),
    path('<int:pk>/technical/', product_technical_api, name='product-technical'),
    path('<int:pk>/allergens/', product_allergens_api, name='product-allergens'),
    path(
        '<int:pk>/allergens/<str:allergen_code>/',
        product_allergen_detail_api,
        name='product-allergen-detail',
    ),
    path('<int:pk>/nutrition/', product_nutrition_api, name='product-nutrition'),
    path('<int:pk>/ingredient-label/', product_ingredient_label_api, name='product-ingredient-label'),
    path('<int:pk>/acceptance/', product_acceptance_api, name='product-acceptance'),
    path('<int:pk>/yield/', product_yield_api, name='product-yield'),
]
