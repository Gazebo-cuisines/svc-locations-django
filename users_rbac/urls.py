from django.urls import path

from users_rbac.audit import audit_list, user_activity, user_audit
from users_rbac.views import login_view
from users_rbac.views_users import (
    me_photo,
    me_view,
    user_detail,
    user_grants,
    user_photo,
    user_reset_password,
    users_collection,
)

urlpatterns = [
    # Sign in with username/email + password; returns Cognito tokens.
    path('login/', login_view, name='users_rbac_login'),

    # Caller's profile + effective grants (for FE nav).
    path('me/', me_view, name='users_rbac_me'),

    # Caller uploads own profile photo (private S3; returns short-lived photo_url).
    path('me/photo/', me_photo, name='users_rbac_me_photo'),

    # Admin: list RBAC audit events (filters + pagination).
    path('audit/', audit_list, name='users_rbac_audit'),

    # Admin: list users (GET) or create Cognito + local user (POST).
    path('users/', users_collection, name='users_rbac_users'),

    # Admin: get one user (GET) or patch display_name / is_active (PATCH).
    path('users/<int:user_id>/', user_detail, name='users_rbac_user_detail'),

    # Admin or self: upload profile photo to User-profile/{cognito_sub}/.
    path('users/<int:user_id>/photo/', user_photo, name='users_rbac_user_photo'),

    # Admin: replace the full grant tree for a user.
    path('users/<int:user_id>/grants/', user_grants, name='users_rbac_user_grants'),

    # Admin: set a new permanent Cognito password.
    path(
        'users/<int:user_id>/reset-password/',
        user_reset_password,
        name='users_rbac_reset_password',
    ),

    # Admin: RBAC audit timeline for one user (as actor/target/both).
    path('users/<int:user_id>/audit/', user_audit, name='users_rbac_user_audit'),

    # Admin: merged day timeline (RBAC + product/stock actions by this user).
    path(
        'users/<int:user_id>/activity/',
        user_activity,
        name='users_rbac_user_activity',
    ),
]
