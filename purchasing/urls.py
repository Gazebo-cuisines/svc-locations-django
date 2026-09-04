from django.urls import path

from purchasing.views import (
    adhoc_goods_in_checklist_api,
    adhoc_goods_in_collection_api,
    adhoc_goods_in_detail_api,
    adhoc_goods_in_header_qc_api,
    adhoc_goods_in_line_qc_api,
    adhoc_goods_in_receive_api,
    legacy_csv_import_api,
    po_amend_api,
    po_attachment_detail_api,
    po_attachments_api,
    po_collection_api,
    po_delivery_attachments_api,
    po_delivery_cancel_api,
    po_delivery_checklist_api,
    po_delivery_collection_api,
    po_delivery_detail_api,
    po_delivery_header_qc_api,
    po_delivery_header_qc_unblock_api,
    po_delivery_line_qc_api,
    po_delivery_print_api,
    po_delivery_receive_api,
    po_detail_api,
    po_goods_in_form_api,
    po_header_qc_api,
    po_line_qc_api,
    po_print_api,
    po_receive_api,
    po_release_api,
    po_timeline_api,
    rejected_deliveries_api,
    stock_adjustment_api,
)
from purchasing.views_check_templates import (
    check_template_collection_api,
    check_template_detail_api,
    check_template_item_detail_api,
    check_template_items_api,
)

urlpatterns = [
    path('check-templates/', check_template_collection_api, name='purchasing-check-template-list'),
    path('check-templates/<int:template_id>/', check_template_detail_api, name='purchasing-check-template-detail'),
    path('check-templates/<int:template_id>/items/', check_template_items_api, name='purchasing-check-template-items'),
    path(
        'check-templates/<int:template_id>/items/<int:item_id>/',
        check_template_item_detail_api,
        name='purchasing-check-template-item-detail',
    ),

    # PO API
    path('pos/', po_collection_api, name='purchasing-po-list'),
    path('pos/<int:po_id>/', po_detail_api, name='purchasing-po-detail'),
    path('pos/<int:po_id>/amend/', po_amend_api, name='purchasing-po-amend'),
    path('pos/<int:po_id>/timeline/', po_timeline_api, name='purchasing-po-timeline'),

    # Nested deliveries (one QC session per truck)
    path('deliveries/rejected/', rejected_deliveries_api, name='purchasing-rejected-deliveries'),
    path('pos/<int:po_id>/deliveries/', po_delivery_collection_api, name='purchasing-po-delivery-list'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/', po_delivery_detail_api, name='purchasing-po-delivery-detail'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/cancel/', po_delivery_cancel_api, name='purchasing-po-delivery-cancel'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/checklist/', po_delivery_checklist_api, name='purchasing-po-delivery-checklist'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/qc/header/', po_delivery_header_qc_api, name='purchasing-po-delivery-header-qc'),
    path(
        'pos/<int:po_id>/deliveries/<int:delivery_id>/qc/header/unblock/',
        po_delivery_header_qc_unblock_api,
        name='purchasing-po-delivery-header-qc-unblock',
    ),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/lines/<int:line_id>/qc/', po_delivery_line_qc_api, name='purchasing-po-delivery-line-qc'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/receive/', po_delivery_receive_api, name='purchasing-po-delivery-receive'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/attachments/', po_delivery_attachments_api, name='purchasing-po-delivery-attachments'),
    path('pos/<int:po_id>/deliveries/<int:delivery_id>/print/', po_delivery_print_api, name='purchasing-po-delivery-print'),

    # PO Goods In Form API (aliases open delivery)
    path('pos/<int:po_id>/goods-in-form/', po_goods_in_form_api, name='purchasing-po-goods-in-form'),

    # PO Header QC API
    path('pos/<int:po_id>/qc/header/', po_header_qc_api, name='purchasing-po-header-qc'),

    # PO Line QC API
    path('pos/<int:po_id>/lines/<int:line_id>/qc/', po_line_qc_api, name='purchasing-po-line-qc'),

    # PO Receive API
    path('pos/<int:po_id>/receive/', po_receive_api, name='purchasing-po-receive'),

    # PO Release API
    path('pos/<int:po_id>/release/', po_release_api, name='purchasing-po-release'),

    # PO Attachments API
    path('pos/<int:po_id>/attachments/', po_attachments_api, name='purchasing-po-attachments'),

    # PO Attachment Detail API
    path('pos/<int:po_id>/attachments/<int:attachment_id>/', po_attachment_detail_api, name='purchasing-po-attachment-detail'),
    path('pos/<int:po_id>/print/', po_print_api, name='purchasing-po-print'),
    path('imports/legacy-csv/', legacy_csv_import_api, name='purchasing-legacy-csv-import'),

    # Without-PO goods-in QC + receive
    path('adhoc-goods-in/', adhoc_goods_in_collection_api, name='purchasing-adhoc-goods-in-start'),
    path('adhoc-goods-in/<int:session_id>/', adhoc_goods_in_detail_api, name='purchasing-adhoc-goods-in-detail'),
    path('adhoc-goods-in/<int:session_id>/checklist/', adhoc_goods_in_checklist_api, name='purchasing-adhoc-goods-in-checklist'),
    path('adhoc-goods-in/<int:session_id>/qc/header/', adhoc_goods_in_header_qc_api, name='purchasing-adhoc-goods-in-header-qc'),
    path('adhoc-goods-in/<int:session_id>/qc/line/', adhoc_goods_in_line_qc_api, name='purchasing-adhoc-goods-in-line-qc'),
    path('adhoc-goods-in/<int:session_id>/receive/', adhoc_goods_in_receive_api, name='purchasing-adhoc-goods-in-receive'),

    # Stock Adjustment (Goods In tab C — no QC)
    path('stock-adjustment/', stock_adjustment_api, name='purchasing-stock-adjustment'),
]
