from functools import wraps

from users_rbac.auth import attach_user
from users_rbac.permissions import require_any_admin


def require_recipe_approver(request):
    """
    Today: IT (global) or any admin-area grant.
    Later: swap require_any_admin for require_admin_area(..., TECHNICAL).
    """
    denied = attach_user(request)
    if denied:
        return denied
    return require_any_admin(request)


def gate_recipe_write(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.method == 'GET':
            return view_func(request, *args, **kwargs)
        denied = attach_user(request)
        if denied:
            return denied
        return view_func(request, *args, **kwargs)

    return wrapped


def gate_recipe_activate(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        denied = require_recipe_approver(request)
        if denied:
            return denied
        return view_func(request, *args, **kwargs)

    return wrapped
