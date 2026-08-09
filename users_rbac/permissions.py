"""Deny-by-default grant checks. Return 403 response or None."""

from core.api_response import error_response
from users_rbac.audit import record_event
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
