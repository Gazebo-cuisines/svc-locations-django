"""Deny-by-default grant checks. Return 403 response or None."""

from functools import wraps

from core.api_response import error_response
from users_rbac.audit import record_event
from users_rbac.auth import attach_user
from users_rbac.grants import PERIOD_FLAGS, grants_dict
from users_rbac.models import (
    AdminAccess,
    ProductionAccess,
    RbacAuditAction,
    WarehouseAccess,
)


def deny_access(request, required: dict):
    held = grants_dict(request.rbac_user)
    record_event(
        request,
        action=RbacAuditAction.AUTH_ACCESS_DENIED,
        actor=request.rbac_user,
        target=request.rbac_user,
        detail_json={'required': required, 'held': held},
    )
    return error_response('You do not have permission to do that.', status_code=403)


def require_any_admin(request):
    if AdminAccess.objects.filter(user=request.rbac_user).exists():
        return None
    return deny_access(request, {'admin': 'any'})


def require_admin_area(request, area: str):
    if AdminAccess.objects.filter(user=request.rbac_user, area=area).exists():
        return None
    return deny_access(request, {'admin_area': area})


def require_production_area(request, area: str):
    if ProductionAccess.objects.filter(user=request.rbac_user, area=area).exists():
        return None
    return deny_access(request, {'production_area': area})


def require_any_production(request):
    if ProductionAccess.objects.filter(user=request.rbac_user).exists():
        return None
    return deny_access(request, {'production': 'any'})


def require_any_warehouse(request, *, action: str | None = None):
    qs = WarehouseAccess.objects.filter(user=request.rbac_user)
    if action == 'goods_in':
        qs = qs.filter(can_goods_in=True)
    elif action == 'goods_out':
        qs = qs.filter(can_goods_out=True)
    if qs.exists():
        return None
    return deny_access(request, {'warehouse': 'any', 'action': action})


def require_floor_write(request):
    if ProductionAccess.objects.filter(user=request.rbac_user).exists():
        return None
    if WarehouseAccess.objects.filter(user=request.rbac_user).exists():
        return None
    return deny_access(request, {'department': 'production|warehouse'})


def _gate_write(view_func, check):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.method == 'GET':
            return view_func(request, *args, **kwargs)
        denied = attach_user(request, missing='ok', invalid='error')
        if denied:
            return denied
        if getattr(request, 'rbac_user', None):
            denied = check(request)
            if denied:
                return denied
        return view_func(request, *args, **kwargs)

    return wrapped


def gate_production_write(view_func):
    return _gate_write(view_func, require_any_production)


def gate_warehouse_write(*, action: str | None = None):
    def decorator(view_func):
        return _gate_write(
            view_func,
            lambda request: require_any_warehouse(request, action=action),
        )

    return decorator


def gate_floor_write(view_func):
    return _gate_write(view_func, require_floor_write)


def require_warehouse(
    request,
    unit: str,
    *,
    action: str | None = None,
    period: str | None = None,
):
    row = WarehouseAccess.objects.filter(user=request.rbac_user, unit=unit).first()
    required = {'warehouse_unit': unit, 'action': action, 'period': period}
    if not row:
        return deny_access(request, required)
    if action == 'goods_in' and not row.can_goods_in:
        return deny_access(request, required)
    if action == 'goods_out' and not row.can_goods_out:
        return deny_access(request, required)
    if period:
        field = PERIOD_FLAGS.get(period)
        if not field or not row.can_goods_in or not getattr(row, field):
            return deny_access(request, required)
    return None
