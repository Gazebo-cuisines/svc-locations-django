from django.urls import path

from product.views.product_master_view import product_detail_api, product_list_api

urlpatterns = [
    path('', product_list_api, name='product-list'),
    path('<int:pk>/', product_detail_api, name='product-detail'),
]
