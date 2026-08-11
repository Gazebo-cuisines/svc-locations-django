from django.urls import path

from purchasing.views import (
    legacy_csv_import_api,
    po_attachment_detail_api,
    po_attachments_api,
    po_collection_api,
    po_detail_api,
    po_goods_in_form_api,
    po_header_qc_api,
    po_line_qc_api,
    po_print_api,
    po_receive_api,
    po_release_api,
)

urlpatterns = [
    # PO
    path('pos/', po_collection_api, name='purchasing-po-list'),
    path('pos/<int:po_id>/', po_detail_api, name='purchasing-po-detail'),
    
    # Goods In Form
    path('pos/<int:po_id>/goods-in-form/', po_goods_in_form_api, name='purchasing-po-goods-in-form'),
    
    # QC
    path('pos/<int:po_id>/qc/header/', po_header_qc_api, name='purchasing-po-header-qc'),
    path('pos/<int:po_id>/lines/<int:line_id>/qc/', po_line_qc_api, name='purchasing-po-line-qc'),
    
    # Receive
    path('pos/<int:po_id>/receive/', po_receive_api, name='purchasing-po-receive'),
    
    # Release
    path('pos/<int:po_id>/release/', po_release_api, name='purchasing-po-release'),
    
    # Attachments
    path('pos/<int:po_id>/attachments/', po_attachments_api, name='purchasing-po-attachments'),
    path('pos/<int:po_id>/attachments/<int:attachment_id>/', po_attachment_detail_api, name='purchasing-po-attachment-detail'),
    
    # Print
    path('pos/<int:po_id>/print/', po_print_api, name='purchasing-po-print'),
    
    # Legacy CSV Import
    path('imports/legacy-csv/', legacy_csv_import_api, name='purchasing-legacy-csv-import'),
]
