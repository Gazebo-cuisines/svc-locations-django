from django.urls import path

from product.views.acceptance_views import product_acceptance_api
from product.views.allergen_views import (
    product_allergen_detail_api,
    product_allergens_api,
)
from product.views.ingredient_label_views import product_ingredient_label_api
from product.views.nutrition_views import product_nutrition_api
from product.views.product_utils import (
    product_category_list_api,
    product_class_api,
    product_class_list_api,
    product_range_api,
    product_range_list_api,
    product_unit_api,
)
from product.views.product_master_view import product_collection_api, product_detail_api
from product.views.technical_views import product_technical_api
from product.views.unit_views import unit_collection_api, unit_detail_api

urlpatterns = [

    # general related urls
    path('', product_collection_api, name='product-collection'),
    path('class/', product_class_list_api, name='product-class-list'),
    path('category/', product_category_list_api, name='product-category-list'),
    path('range/', product_range_list_api, name='product-range-list'),
    path('unit/', unit_collection_api, name='product-unit-list'),
    path('unit/<int:pk>/', unit_detail_api, name='product-unit-detail'),
    path('<int:pk>/', product_detail_api, name='product-detail'),

    # complaince related urls
    path('<int:pk>/technical/', product_technical_api, name='product-technical'),
    path('<int:pk>/allergens/', product_allergens_api, name='product-allergens'),
    path( '<int:pk>/allergens/<str:allergen_code>/', product_allergen_detail_api, name='product-allergen-detail'),
    path('<int:pk>/nutrition/', product_nutrition_api, name='product-nutrition'),
    path('<int:pk>/ingredient-label/', product_ingredient_label_api, name='product-ingredient-label'),
    path('<int:pk>/acceptance/', product_acceptance_api, name='product-acceptance'),
   
    # product related urls
    path('<int:pk>/class/', product_class_api, name='product-class'),
    path('<int:pk>/range/', product_range_api, name='product-range'),
    path('<int:pk>/unit/', product_unit_api, name='product-unit'),
    
]
