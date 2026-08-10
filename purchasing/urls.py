from django.urls import path

from purchasing.views import po_collection_api, po_detail_api

urlpatterns = [
    path('pos/', po_collection_api, name='purchasing-po-list'),
    path('pos/<int:po_id>/', po_detail_api, name='purchasing-po-detail'),
]
