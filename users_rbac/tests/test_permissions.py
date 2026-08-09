import json

from django.test import RequestFactory, TestCase

from users_rbac.models import (
    AdminAccess,
    AdminArea,
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
from users_rbac.permissions import (
    require_admin_area,
    require_any_admin,
    require_any_production,
    require_any_warehouse,
    require_floor_write,
    require_production_area,
    require_warehouse,
)


class PermissionHelperTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )
        UserDepartment.objects.create(user=self.user, department=Department.PRODUCTION)
        UserDepartment.objects.create(user=self.user, department=Department.WAREHOUSE)
        ProductionAccess.objects.create(user=self.user, area=ProductionArea.LOW_RISK)
        WarehouseAccess.objects.create(
            user=self.user,
            unit=WarehouseUnit.UNIT_1,
            can_goods_in=True,
            goods_in_today=True,
        )

    def _req(self):
        request = self.factory.get('/x/', REMOTE_ADDR='10.0.1.22')
        request.rbac_user = self.user
        return request

    def test_production_area_ok(self):
        self.assertIsNone(require_production_area(self._req(), ProductionArea.LOW_RISK))

    def test_production_area_denied_audits(self):
        request = self._req()
        response = require_production_area(request, ProductionArea.HIGH_RISK)
        self.assertEqual(response.status_code, 403)
        event = RbacAuditEvent.objects.get()
        self.assertEqual(event.action, RbacAuditAction.AUTH_ACCESS_DENIED)
        self.assertEqual(event.actor_username, 'amit01')
        self.assertEqual(event.detail_json['required'], {'production_area': 'high_risk'})
        self.assertEqual(event.detail_json['held']['production_areas'], ['low_risk'])
        self.assertEqual(event.source_ip, '10.0.1.22')

    def test_warehouse_today_ok(self):
        self.assertIsNone(
            require_warehouse(
                self._req(),
                WarehouseUnit.UNIT_1,
                action='goods_in',
                period='today',
            )
        )

    def test_warehouse_goods_out_denied(self):
        response = require_warehouse(
            self._req(),
            WarehouseUnit.UNIT_1,
            action='goods_out',
        )
        self.assertEqual(response.status_code, 403)

    def test_warehouse_future_denied(self):
        response = require_warehouse(
            self._req(),
            WarehouseUnit.UNIT_1,
            action='goods_in',
            period='future',
        )
        self.assertEqual(response.status_code, 403)
        detail = json.loads(response.content)
        self.assertIn('permission', detail['message'])

    def test_warehouse_other_unit_denied(self):
        response = require_warehouse(self._req(), WarehouseUnit.UNIT_2)
        self.assertEqual(response.status_code, 403)

    def test_admin_area_ok_and_denied(self):
        AdminAccess.objects.create(user=self.user, area=AdminArea.TECHNICAL)
        self.assertIsNone(require_admin_area(self._req(), AdminArea.TECHNICAL))
        response = require_admin_area(self._req(), AdminArea.FINANCE)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            RbacAuditEvent.objects.latest('id').detail_json['required'],
            {'admin_area': 'finance'},
        )

    def test_any_production_and_warehouse(self):
        self.assertIsNone(require_any_production(self._req()))
        self.assertIsNone(require_any_warehouse(self._req(), action='goods_in'))
        self.assertEqual(
            require_any_warehouse(self._req(), action='goods_out').status_code,
            403,
        )
        self.assertIsNone(require_floor_write(self._req()))

    def test_it_has_global_rights(self):
        it = RbacUser.objects.create(
            cognito_sub='sub-it',
            username='it01',
            display_name='IT',
        )
        UserDepartment.objects.create(user=it, department=Department.IT)
        request = self.factory.get('/x/', REMOTE_ADDR='10.0.1.22')
        request.rbac_user = it
        self.assertIsNone(require_any_admin(request))
        self.assertIsNone(require_admin_area(request, AdminArea.FINANCE))
        self.assertIsNone(require_production_area(request, ProductionArea.HIGH_RISK))
        self.assertIsNone(
            require_warehouse(
                request,
                WarehouseUnit.UNIT_2,
                action='goods_out',
            )
        )
        self.assertIsNone(require_floor_write(request))

    def test_any_admin_denied_for_floor(self):
        response = require_any_admin(self._req())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            RbacAuditEvent.objects.get().detail_json['required'],
            {'admin': 'any'},
        )
