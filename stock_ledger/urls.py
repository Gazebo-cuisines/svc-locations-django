from django.urls import path

from stock_ledger import views

urlpatterns = [
    path('lots/', views.lots_collection_api, name='stock-lots'),
    path('lots/<int:pk>/', views.lot_detail_api, name='stock-lot-detail'),
    path('unit-conversions/', views.unit_conversions_api, name='stock-unit-conversions'),
    # Stock movement endpoints
    path('receipt/', views.receipt_api, name='stock-receipt'),
    path('issue/', views.issue_api, name='stock-issue'),
    # Stock receipt endpoints
    # Stock transfer endpoints
    path('transfer/', views.transfer_api, name='stock-transfer'),
    # Stock disposal endpoints
    path('disposal/', views.disposal_api, name='stock-disposal'),
    # Stock count adjustment endpoints
    path('count-adjustment/', views.count_adjustment_api, name='stock-count-adjustment'),
    # Stock reversal endpoints
    path('reversal/', views.reversal_api, name='stock-reversal'),
    # Stock entry detail endpoints
    path('entries/<int:pk>/', views.entry_detail_api, name='stock-entry-detail'),
    # Stock balance endpoints
    path('balances/', views.balance_list_api, name='stock-balances'),
    
    # Stock ATP endpoints
    path('atp/', views.atp_api, name='stock-atp'),

    # Stock trace endpoints
    path('trace/backward/', views.trace_backward_api, name='stock-trace-backward'),
    path('trace/forward/', views.trace_forward_api, name='stock-trace-forward'),
    path('entries/<int:entry_id>/mass-balance/', views.mass_balance_api, name='stock-mass-balance'),

    # Stock reservation endpoints
    path('reservations/', views.reservation_create_api, name='stock-reservation-create'),
    path('reservations/<int:pk>/release/', views.reservation_release_api, name='stock-reservation-release'),
    # Stock reservation consume endpoints
    path('reservations/<int:pk>/consume/', views.reservation_consume_api, name='stock-reservation-consume'),
]
