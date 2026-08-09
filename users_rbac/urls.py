from django.urls import path

from users_rbac.audit import audit_list, user_audit
from users_rbac.views import login_view
from users_rbac.views_users import (
    me_view,
    user_detail,
    user_grants,
    user_reset_password,
    users_collection,
)

urlpatterns = [
    path('login/', login_view, name='users_rbac_login'),
    path('me/', me_view, name='users_rbac_me'),
    path('audit/', audit_list, name='users_rbac_audit'),
    path('users/', users_collection, name='users_rbac_users'),
    path('users/<int:user_id>/', user_detail, name='users_rbac_user_detail'),
    path('users/<int:user_id>/grants/', user_grants, name='users_rbac_user_grants'),
    path(
        'users/<int:user_id>/reset-password/',
        user_reset_password,
        name='users_rbac_reset_password',
    ),
    path('users/<int:user_id>/audit/', user_audit, name='users_rbac_user_audit'),
]
