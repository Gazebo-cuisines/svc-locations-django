from django.urls import path

from product.views.acceptance_views import product_acceptance_api
from product.views.allergen_views import product_allergens_api
from product.views.ingredient_label_views import product_ingredient_label_api
from product.views.nutrition_views import product_nutrition_api
from product.views.product_master_view import product_detail_api, product_list_api
from product.views.technical_views import product_technical_api

urlpatterns = [
    path('', product_list_api, name='product-list'),
    path('<int:pk>/', product_detail_api, name='product-detail'),
    path('<int:pk>/technical/', product_technical_api, name='product-technical'),
    path('<int:pk>/allergens/', product_allergens_api, name='product-allergens'),
    path('<int:pk>/nutrition/', product_nutrition_api, name='product-nutrition'),
    path(
        '<int:pk>/ingredient-label/',
        product_ingredient_label_api,
        name='product-ingredient-label',
    ),
    path('<int:pk>/acceptance/', product_acceptance_api, name='product-acceptance'),
]
