from django.urls import path

from users_rbac.views import login_view

urlpatterns = [
    path("login/", login_view, name="users_rbac_login"),
]
