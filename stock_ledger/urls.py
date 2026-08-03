from django.urls import path

from stock_ledger import views

# MVP happy path: lots (hidden resolve), movements, production, balances.
# Parked for later (keep routes; not required for floor/ops MVP):
#   - ATP / reservations
#   - genealogy trace + mass-balance (needs kg edges)
#   - unit-conversions as a gate (moves no longer require kg)
#   - S3 chain anchors / hash verify commands

urlpatterns = [
    path('lots/', views.lots_collection_api, name='stock-lots'),
    path('lots/<int:pk>/', views.lot_detail_api, name='stock-lot-detail'),
    path('unit-conversions/', views.unit_conversions_api, name='stock-unit-conversions'),
    path('receipt/', views.receipt_api, name='stock-receipt'),
    path('issue/', views.issue_api, name='stock-issue'),
    path('production/', views.production_api, name='stock-production'),
    path(
        'production/<int:entry_id>/',
        views.production_detail_api,
        name='stock-production-detail',
    ),
    path('downtime/', views.downtime_api, name='stock-downtime'),
    path(
        'production/<int:entry_id>/requirements/',
        views.production_requirements_api,
        name='stock-production-requirements',
    ),
    path(
        'production/<int:entry_id>/consume/',
        views.production_consume_api,
        name='stock-production-consume',
    ),
    path('transfer/', views.transfer_api, name='stock-transfer'),
    path('disposal/', views.disposal_api, name='stock-disposal'),
    path('count-adjustment/', views.count_adjustment_api, name='stock-count-adjustment'),
    path('reversal/', views.reversal_api, name='stock-reversal'),
    path('entries/<int:pk>/', views.entry_detail_api, name='stock-entry-detail'),
    path('balances/', views.balance_list_api, name='stock-balances'),
    path('balances/stream/', views.balance_stream_api, name='stock-balances-stream'),
    # --- parked (non-MVP) ---
    path('atp/', views.atp_api, name='stock-atp'),
    path('trace/backward/', views.trace_backward_api, name='stock-trace-backward'),
    path('trace/forward/', views.trace_forward_api, name='stock-trace-forward'),
    path('entries/<int:entry_id>/mass-balance/', views.mass_balance_api, name='stock-mass-balance'),
    path('reservations/', views.reservation_create_api, name='stock-reservation-create'),
    path('reservations/<int:pk>/release/', views.reservation_release_api, name='stock-reservation-release'),
    path('reservations/<int:pk>/consume/', views.reservation_consume_api, name='stock-reservation-consume'),
]
