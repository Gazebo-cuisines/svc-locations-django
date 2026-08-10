from django.urls import path

from purchasing.views import (
    po_collection_api,
    po_detail_api,
    po_goods_in_form_api,
    po_header_qc_api,
    po_line_qc_api,
    po_receive_api,
    po_release_api,
)

urlpatterns = [
    path('pos/', po_collection_api, name='purchasing-po-list'),
    path('pos/<int:po_id>/', po_detail_api, name='purchasing-po-detail'),
    path(
        'pos/<int:po_id>/goods-in-form/',
        po_goods_in_form_api,
        name='purchasing-po-goods-in-form',
    ),
    path(
        'pos/<int:po_id>/qc/header/',
        po_header_qc_api,
        name='purchasing-po-header-qc',
    ),
    path(
        'pos/<int:po_id>/lines/<int:line_id>/qc/',
        po_line_qc_api,
        name='purchasing-po-line-qc',
    ),
    path(
        'pos/<int:po_id>/receive/',
        po_receive_api,
        name='purchasing-po-receive',
    ),
    path(
        'pos/<int:po_id>/release/',
        po_release_api,
        name='purchasing-po-release',
    ),
]
