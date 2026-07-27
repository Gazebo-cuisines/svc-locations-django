from django.urls import path

from product.views.product_master_view import product_collection_api, product_detail_api

urlpatterns = [
    path('', product_collection_api, name='product-collection'),
    path('<int:pk>/', product_detail_api, name='product-detail'),
]
