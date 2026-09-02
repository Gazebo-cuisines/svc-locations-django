from django.urls import path

from stock_ledger import views

# MVP happy path: lots (hidden resolve), movements, production, balances.
# Parked for later (keep routes; not required for floor/ops MVP):
#   - ATP / reservations
#   - unit-conversions as a gate (moves no longer require kg)
#   - S3 chain anchors / hash verify commands

urlpatterns = [
    # lots
    path('lots/', views.lots_collection_api, name='stock-lots'),
    path('lots/<int:pk>/', views.lot_detail_api, name='stock-lot-detail'),

    # unit conversions
    path('unit-conversions/', views.unit_conversions_api, name='stock-unit-conversions'),

    # movements
    # Goods In logic
    path('receipt/', views.receipt_api, name='stock-receipt'),

    # Goods Out logic
    path('issue/', views.issue_api, name='stock-issue'),
    path('production/', views.production_api, name='stock-production'),
    path('production/<int:entry_id>/', views.production_detail_api, name='stock-production-detail'),
    
    # downtime
    path('downtime/', views.downtime_api, name='stock-downtime'),
    
    # production
    path('production/<int:entry_id>/requirements/', views.production_requirements_api, name='stock-production-requirements'),
    path('production/<int:entry_id>/allocation-status/',views.production_allocation_status_api, name='stock-production-allocation-status',),
    path('production/<int:entry_id>/consume/', views.production_consume_api, name='stock-production-consume'),
   
    path('transfer/', views.transfer_api, name='stock-transfer'),
    path('disposal/', views.disposal_api, name='stock-disposal'),

    # stock reconciliation logic
    path('count-adjustment/', views.count_adjustment_api, name='stock-count-adjustment'),
    path('reversal/', views.reversal_api, name='stock-reversal'),

    # goods out scan logic
    path('scan/goods-out/', views.scan_goods_out_api, name='stock-scan-goods-out'),
    path(
        'goods-out/suggest/',
        views.goods_out_suggest_api,
        name='stock-goods-out-suggest',
    ),

    path('scan/', views.scan_resolve_api, name='stock-scan'),
    path('recall/', views.recall_api, name='stock-recall'),
    path('products/<int:product_id>/genealogy/', views.product_genealogy_api, name='stock-product-genealogy'),
    path('products/<int:product_id>/label/', views.product_label_api, name='stock-product-label'),
    path('entries/<int:entry_id>/label/', views.entry_label_api, name='stock-entry-label'),
    path('entries/<int:entry_id>/labels/print/', views.entry_label_print_api, name='stock-entry-label-print'),
    path('entries/<int:entry_id>/labels/verify/', views.entry_label_verify_api, name='stock-entry-label-verify'),
    path('entries/<int:entry_id>/labels/activity/', views.entry_label_activity_api, name='stock-entry-label-activity'),
    path('entries/<int:entry_id>/post/', views.entry_post_api, name='stock-entry-post'),
    path('entries/<int:entry_id>/cancel/', views.entry_cancel_api, name='stock-entry-cancel'),
    path('entries/queued/', views.entry_queued_list_api, name='stock-entry-queued'),
    path('stock-units/print/', views.stock_units_print_api, name='stock-units-print'),
    path('stock-units/<str:unit_serial>/consume/', views.stock_units_consume_api, name='stock-units-consume'),
    path('stock-units/<str:unit_serial>/void/', views.stock_units_void_api, name='stock-units-void'),
    path('stock-units/<str:unit_serial>/reprint/', views.stock_units_reprint_api, name='stock-units-reprint'),
    path('stock-units/<str:unit_serial>/', views.stock_units_detail_api, name='stock-units-detail'),
    path('entries/<int:pk>/', views.entry_detail_api, name='stock-entry-detail'),
    path('audit/timeline/', views.audit_timeline_api, name='stock-audit-timeline'),
    path('reports/goods-in/', views.goods_in_report_api, name='stock-report-goods-in'),
    path('reports/goods-out/', views.goods_out_report_api, name='stock-report-goods-out'),
    path(
        'reports/closing-stock/',
        views.closing_stock_report_api,
        name='stock-report-closing-stock',
    ),
    path(
        'reports/operator-activity/',
        views.operator_activity_report_api,
        name='stock-report-operator-activity',
    ),
    path(
        'reports/operator-activity/detail/',
        views.operator_activity_detail_api,
        name='stock-report-operator-activity-detail',
    ),
    path(
        'reports/email-recipients/',
        views.report_email_recipients_api,
        name='stock-report-email-recipients',
    ),
    path(
        'reports/email-recipients/<int:pk>/',
        views.report_email_recipient_detail_api,
        name='stock-report-email-recipient-detail',
    ),
    path(
        'reports/email-unsubscribe/',
        views.report_email_unsubscribe_api,
        name='stock-report-email-unsubscribe',
    ),
    path('balances/', views.balance_list_api, name='stock-balances'),
    path('balances/stream/', views.balance_stream_api, name='stock-balances-stream'),
    path('warehouse/remaining/', views.warehouse_remaining_api, name='stock-warehouse-remaining'),
    path('manage/ping/', views.manage_ping_api, name='stock-manage-ping'),
    path('manage/entries/', views.manage_entries_list_api, name='stock-manage-entries-list'),
    path(
        'manage/entries/<int:entry_id>/',
        views.manage_entry_preview_api,
        name='stock-manage-entry-preview',
    ),
    path(
        'manage/entries/<int:entry_id>/remove/',
        views.manage_entry_remove_api,
        name='stock-manage-entry-remove',
    ),
    # --- parked (non-MVP) ---
    path('atp/', views.atp_api, name='stock-atp'),
    path('trace/backward/', views.trace_backward_api, name='stock-trace-backward'),
    path('trace/forward/', views.trace_forward_api, name='stock-trace-forward'),
    path('entries/<int:entry_id>/mass-balance/', views.mass_balance_api, name='stock-mass-balance'),
    path('reservations/', views.reservation_create_api, name='stock-reservation-create'),
    path('reservations/<int:pk>/release/', views.reservation_release_api, name='stock-reservation-release'),
    path('reservations/<int:pk>/consume/', views.reservation_consume_api, name='stock-reservation-consume'),
]
