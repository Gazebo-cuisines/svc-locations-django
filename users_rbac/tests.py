from django.db import IntegrityError
from django.test import TestCase

from users_rbac.models import (
    Department,
    ProductionAccess,
    ProductionArea,
    RbacAuditAction,
    RbacAuditEvent,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)


class RbacSchemaTests(TestCase):
    def test_user_grants_and_audit_row(self):
        user = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )
        UserDepartment.objects.create(user=user, department=Department.PRODUCTION)
        ProductionAccess.objects.create(user=user, area=ProductionArea.LOW_RISK)
        WarehouseAccess.objects.create(
            user=user,
            unit=WarehouseUnit.UNIT_1,
            can_goods_in=True,
            goods_in_today=True,
        )
        event = RbacAuditEvent.objects.create(
            action=RbacAuditAction.AUTH_LOGIN_SUCCESS,
            actor_sub=user.cognito_sub,
            actor_username=user.username,
            target_user=user,
            source_ip='10.0.1.22',
        )
        self.assertEqual(str(user), 'amit01')
        self.assertEqual(user.departments.count(), 1)
        self.assertEqual(user.production_access.get().area, ProductionArea.LOW_RISK)
        self.assertTrue(user.warehouse_access.get().can_goods_in)
        self.assertEqual(event.action, RbacAuditAction.AUTH_LOGIN_SUCCESS)

    def test_duplicate_department_rejected(self):
        user = RbacUser.objects.create(cognito_sub='sub-1', username='jane')
        UserDepartment.objects.create(user=user, department=Department.ADMIN)
        with self.assertRaises(IntegrityError):
            UserDepartment.objects.create(user=user, department=Department.ADMIN)
