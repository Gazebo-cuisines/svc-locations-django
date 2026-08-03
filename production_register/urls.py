from django.urls import path

from production_register import views

urlpatterns = [
    path('stations/', views.stations_list_api, name='production-stations'),
    path('stations/<str:code>/', views.station_detail_api, name='production-station-detail'),
    path(
        'stations/<str:code>/downtime/',
        views.station_downtime_api,
        name='production-station-downtime',
    ),
    path('runs/', views.runs_collection_api, name='production-runs'),
    path('runs/<int:run_id>/', views.run_detail_api, name='production-run-detail'),
    path(
        'runs/<int:run_id>/preview-consume/',
        views.preview_consume_api,
        name='production-preview-consume',
    ),
    path(
        'runs/<int:run_id>/consumptions/',
        views.consumptions_put_api,
        name='production-consumptions',
    ),
    path('runs/<int:run_id>/post/', views.post_run_api, name='production-post'),
    path('runs/<int:run_id>/void/', views.void_run_api, name='production-void'),
    path(
        'downtime/<int:downtime_id>/',
        views.downtime_delete_api,
        name='production-downtime-delete',
    ),
]
