from django.urls import path

from product.views.acceptance_views import product_acceptance_api
from product.views.allergen_views import (
    product_allergen_detail_api,
    product_allergens_api,
)
from product.views.audit_views import product_stock_overrides_api, product_timeline_api
from product.views.costing_views import product_costing_api
from product.views.flags_views import product_flags_api
from product.views.image_views import product_image_detail_api, product_images_api
from product.views.ingredient_label_views import product_ingredient_label_api
from product.views.lookups_views import (
    product_allergen_code_list_api,
    product_category_detail_api,
    product_category_image_api,
    product_category_list_api,
    product_class_list_api,
    product_delivery_state_list_api,
    product_packaging_type_list_api,
    product_physical_state_list_api,
    product_purchase_format_detail_api,
    product_purchase_format_list_api,
    product_range_list_api,
    product_sub_range_list_api,
    product_unit_detail_api,
    product_unit_list_api,
    product_list_fromcontainer_api
)
from product.views.nutrition_views import product_nutrition_api
from product.views.packaging_views import product_packaging_api
from product.views.product_master_view import product_collection_api, product_detail_api
from product.views.template_views import (
    product_high_risk_create_api,
    product_sleeving_create_api,
)
from product.views.production_views import product_production_api
from product.views.shelf_life_views import product_shelf_life_api
from product.views.stock_policy_views import product_stock_policy_api
from product.views.supplier_product_views import (
    product_supplier_detail_api,
    product_suppliers_api,
    supplier_products_list_api,
)
from product.views.technical_views import product_technical_api
from product.views.yield_views import product_yield_api

urlpatterns = [

    # lookup urls
    path('class/', product_class_list_api, name='product-class-list'),
    
    # category urls
    path('category/', product_category_list_api, name='product-category-list'),
    path('category/<int:pk>/', product_category_detail_api, name='product-category-detail'),
    path('category/<int:pk>/image/', product_category_image_api, name='product-category-image'),
    
    path('range/', product_range_list_api, name='product-range-list'),
    path('sub-range/', product_sub_range_list_api, name='product-sub-range-list'),
    path('unit/', product_unit_list_api, name='product-unit-list'),
    path('unit/<int:pk>/', product_unit_detail_api, name='product-unit-detail'),
    path('purchase-format/', product_purchase_format_list_api, name='product-purchase-format-list'),
    path(
        'purchase-format/<int:pk>/',
        product_purchase_format_detail_api,
        name='product-purchase-format-detail',
    ),
    path('packaging-type/', product_packaging_type_list_api, name='product-packaging-type-list'),
    path('physical-state/', product_physical_state_list_api, name='product-physical-state-list'),
    path('delivery-state/', product_delivery_state_list_api, name='product-delivery-state-list'),
    path('allergen-code/', product_allergen_code_list_api, name='product-allergen-code-list'),
    path('supplier-products/', supplier_products_list_api, name='supplier-products-list'),
    path('sleeving/', product_sleeving_create_api, name='product-sleeving-create'),
    path('high-risk/', product_high_risk_create_api, name='product-high-risk-create'),
    path('', product_collection_api, name='product-collection'),
    path('<int:pk>/', product_detail_api, name='product-detail'),
    path('<int:pk>/images/', product_images_api, name='product-images'),
    path('images/<int:pk>/', product_image_detail_api, name='product-image-detail'),
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
    path('<int:pk>/costing/', product_costing_api, name='product-costing'),
    path('<int:pk>/shelf-life/', product_shelf_life_api, name='product-shelf-life'),
    path('<int:pk>/stock-policy/', product_stock_policy_api, name='product-stock-policy'),
    path('<int:pk>/packaging/', product_packaging_api, name='product-packaging'),
    path('<int:pk>/production/', product_production_api, name='product-production'),
    path('<int:pk>/suppliers/', product_suppliers_api, name='product-suppliers'),
    path(
        '<int:pk>/suppliers/<int:row_id>/',
        product_supplier_detail_api,
        name='product-supplier-detail',
    ),
    path('<int:pk>/timeline/', product_timeline_api, name='product-timeline'),
    path(
        '<int:pk>/stock-overrides/',
        product_stock_overrides_api,
        name='product-stock-overrides',
    ),

    # departement product urls
    path('list/fromcontainer/<int:container_id>/', product_list_fromcontainer_api, name='product-list-fromcontainer'),

]
