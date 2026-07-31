from django.urls import path

from planning import views

urlpatterns = [
    path('plans/', views.plans_collection_api, name='planning-plans'),
    path('plans/<int:plan_id>/', views.plan_detail_api, name='planning-plan-detail'),
    path('plans/<int:plan_id>/lock/', views.plan_lock_api, name='planning-plan-lock'),
    path('plans/<int:plan_id>/close/', views.plan_close_api, name='planning-plan-close'),
    path('plans/<int:plan_id>/reopen/', views.plan_reopen_api, name='planning-plan-reopen'),
    path('plans/<int:plan_id>/commit/', views.plan_commit_api, name='planning-plan-commit'),
    path('plans/<int:plan_id>/events/', views.plan_events_api, name='planning-plan-events'),
    path('plans/<int:plan_id>/lines/', views.plan_lines_api, name='planning-plan-lines'),
    path(
        'plans/<int:plan_id>/lines/<int:line_id>/',
        views.plan_line_detail_api,
        name='planning-plan-line-detail',
    ),
    path('plans/<int:plan_id>/runs/', views.plan_runs_api, name='planning-plan-runs'),
    path(
        'plans/<int:plan_id>/runs/<int:run_id>/',
        views.plan_run_detail_api,
        name='planning-plan-run-detail',
    ),
    path(
        'plans/<int:plan_id>/runs/<int:run_id>/requirements/',
        views.plan_run_requirements_api,
        name='planning-plan-run-requirements',
    ),
    path(
        'requirements/<int:requirement_id>/allocations/',
        views.requirement_allocations_api,
        name='planning-requirement-allocations',
    ),
    path(
        'allocations/<int:allocation_id>/',
        views.allocation_detail_api,
        name='planning-allocation-detail',
    ),
    path('supply/', views.supply_collection_api, name='planning-supply'),
    path('supply/<int:supply_id>/', views.supply_detail_api, name='planning-supply-detail'),
    path('resources/', views.resources_collection_api, name='planning-resources'),
    path(
        'resources/<int:resource_id>/',
        views.resource_detail_api,
        name='planning-resource-detail',
    ),
    path('board/', views.board_api, name='planning-board'),
    path('board/sequence/', views.board_sequence_api, name='planning-board-sequence'),
    path('board/reorder/', views.board_reorder_api, name='planning-board-reorder'),
    path('board/assign/', views.board_assign_api, name='planning-board-assign'),
    path('board/unschedule/', views.board_unschedule_api, name='planning-board-unschedule'),
    path(
        'demand-profiles/',
        views.demand_profiles_collection_api,
        name='planning-demand-profiles',
    ),
    path(
        'demand-profiles/<int:profile_id>/',
        views.demand_profile_detail_api,
        name='planning-demand-profile-detail',
    ),
    path('forecast/horizon/', views.forecast_horizon_api, name='planning-forecast-horizon'),
    path('forecast/shortage/', views.forecast_shortage_api, name='planning-forecast-shortage'),
    path('forecast/age/', views.forecast_age_api, name='planning-forecast-age'),
]
