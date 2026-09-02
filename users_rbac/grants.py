"""Apply and serialize RBAC grants."""

from django.db import transaction

from users_rbac.audit import record_event
from users_rbac.models import (
    GOODS_IN_ACTIONS,
    WAREHOUSE_ACTION_FIELDS,
    AdminAccess,
    AdminArea,
    Department,
    ProductionAccess,
    ProductionArea,
    RbacAuditAction,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)
from users_rbac.photos import photo_url

GRANT_KEYS = ('departments', 'production_areas', 'warehouse', 'admin_areas')

# Storage Location PKs (legacy tblcontainers). unit_1 has no row yet.
WAREHOUSE_UNIT_LOCATION_IDS = {
    WarehouseUnit.UNIT_2: 8,
    WarehouseUnit.UNIT_11: 2,
}


def has_global_access(user: RbacUser) -> bool:
    """Deprecated bypass — access is grant-based only."""
    return False


def is_admin_user(user: RbacUser) -> bool:
    return has_global_access(user) or AdminAccess.objects.filter(user=user).exists()
PERIOD_FLAGS = {
    'previous': 'goods_in_previous',
    'today': 'goods_in_today',
    'future': 'goods_in_future',
}


def _choice_values(enum) -> set[str]:
    return {value for value, _label in enum.choices}


def _ensure_values(values, enum, label: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f'{label} must be a list.')
    allowed = _choice_values(enum)
    out = []
    seen = set()
    for item in values:
        if not isinstance(item, str) or item not in allowed:
            raise ValueError(f'Unknown {label}: {item}.')
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_grants(body: dict) -> dict | None:
    if not any(key in body for key in GRANT_KEYS):
        return None
    return {key: body.get(key) or [] for key in GRANT_KEYS}


def validate_grants(payload: dict) -> dict:
    departments = _ensure_values(payload.get('departments') or [], Department, 'department')
    production_areas = _ensure_values(
        payload.get('production_areas') or [], ProductionArea, 'production area'
    )
    admin_areas = _ensure_values(payload.get('admin_areas') or [], AdminArea, 'admin area')
    warehouse_in = payload.get('warehouse') or []
    if not isinstance(warehouse_in, list):
        raise ValueError('warehouse must be a list.')

    dept_set = set(departments)
    if production_areas and Department.PRODUCTION not in dept_set:
        raise ValueError('Production areas need the production department.')
    if admin_areas and Department.ADMIN not in dept_set:
        raise ValueError('Admin areas need the admin department.')
    if warehouse_in and Department.WAREHOUSE not in dept_set:
        raise ValueError('Warehouse units need the warehouse department.')

    warehouse = []
    seen_units = set()
    allowed_units = _choice_values(WarehouseUnit)
    for row in warehouse_in:
        if not isinstance(row, dict):
            raise ValueError('Each warehouse grant must be an object.')
        unit = row.get('unit')
        if unit not in allowed_units:
            raise ValueError(f'Unknown warehouse unit: {unit}.')
        if unit in seen_units:
            raise ValueError(f'Duplicate warehouse unit: {unit}.')
        seen_units.add(unit)
        actions = row.get('actions') or []
        periods = row.get('goods_in_periods') or []
        if not isinstance(actions, list) or not isinstance(periods, list):
            raise ValueError('Warehouse actions and periods must be lists.')
        allowed_actions = set(WAREHOUSE_ACTION_FIELDS)
        unknown_actions = [a for a in actions if a not in allowed_actions]
        if unknown_actions:
            raise ValueError(f'Unknown warehouse action: {unknown_actions[0]}.')
        unknown_periods = [p for p in periods if p not in PERIOD_FLAGS]
        if unknown_periods:
            raise ValueError(f'Unknown goods in period: {unknown_periods[0]}.')
        action_set = set(actions)
        has_goods_in = bool(action_set & GOODS_IN_ACTIONS)
        if periods and not has_goods_in:
            raise ValueError('Goods in periods need a goods_in action.')
        grant = {
            field: action in action_set
            for action, field in WAREHOUSE_ACTION_FIELDS.items()
        }
        grant['unit'] = unit
        grant['goods_in_previous'] = has_goods_in and 'previous' in periods
        grant['goods_in_today'] = has_goods_in and 'today' in periods
        grant['goods_in_future'] = has_goods_in and 'future' in periods
        warehouse.append(grant)
    return {
        'departments': departments,
        'production_areas': production_areas,
        'admin_areas': admin_areas,
        'warehouse': warehouse,
    }


def grants_dict(user: RbacUser) -> dict:
    warehouse = []
    for row in WarehouseAccess.objects.filter(user=user):
        actions = [
            action
            for action, field in WAREHOUSE_ACTION_FIELDS.items()
            if getattr(row, field)
        ]
        periods = [
            name
            for name, field in PERIOD_FLAGS.items()
            if getattr(row, field)
        ]
        warehouse.append(
            {
                'unit': row.unit,
                'actions': actions,
                'goods_in_periods': periods,
                'location_id': WAREHOUSE_UNIT_LOCATION_IDS.get(row.unit),
            }
        )
    return {
        'departments': list(
            UserDepartment.objects.filter(user=user).values_list('department', flat=True)
        ),
        'production_areas': list(
            ProductionAccess.objects.filter(user=user).values_list('area', flat=True)
        ),
        'warehouse': warehouse,
        'admin_areas': list(
            AdminAccess.objects.filter(user=user).values_list('area', flat=True)
        ),
    }


def user_dict(
    user: RbacUser, *, with_grants: bool = True,
) -> dict:
    data = {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'display_name': user.display_name,
        'photo_url': photo_url(user),
        'is_active': user.is_active,
        'is_admin': is_admin_user(user),
        'cognito_sub': user.cognito_sub,
        'created_at': user.created_at.isoformat() if user.created_at else None,
    }
    if with_grants:
        data.update(grants_dict(user))
    return data


def apply_grants(user: RbacUser, payload: dict, *, request=None) -> dict:
    before = grants_dict(user)
    parsed = validate_grants(payload)
    with transaction.atomic():
        UserDepartment.objects.filter(user=user).delete()
        ProductionAccess.objects.filter(user=user).delete()
        WarehouseAccess.objects.filter(user=user).delete()
        AdminAccess.objects.filter(user=user).delete()
        for department in parsed['departments']:
            UserDepartment.objects.create(user=user, department=department)
        for area in parsed['production_areas']:
            ProductionAccess.objects.create(user=user, area=area)
        for area in parsed['admin_areas']:
            AdminAccess.objects.create(user=user, area=area)
        for row in parsed['warehouse']:
            WarehouseAccess.objects.create(user=user, **row)
        after = grants_dict(user)
        if request is not None:
            record_event(
                request,
                action=RbacAuditAction.GRANTS_REPLACED,
                target=user,
                before_json=before,
                after_json=after,
            )
    return after
