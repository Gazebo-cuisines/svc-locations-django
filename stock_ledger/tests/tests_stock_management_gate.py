"""Stock Management Tool RBAC gate."""

from unittest.mock import patch

from django.test import Client, TestCase

from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)


class StockManagementGateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager = RbacUser.objects.create(
            cognito_sub='sub-mgr',
            username='mgr01',
            display_name='Manager',
        )
        UserDepartment.objects.create(
            user=self.manager,
            department=Department.ADMIN,
        )
        AdminAccess.objects.create(
            user=self.manager,
            area=AdminArea.STOCK_MANAGEMENT,
        )
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-wh',
            username='wh01',
            display_name='Warehouse',
        )
        UserDepartment.objects.create(
            user=self.floor,
            department=Department.WAREHOUSE,
        )
        WarehouseAccess.objects.create(
            user=self.floor,
            unit=WarehouseUnit.UNIT_2,
            can_goods_in=True,
            can_goods_out=True,
        )

    def _get_as(self, user):
        with patch('users_rbac.permissions.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = user
                return None

            mock_attach.side_effect = _set_user
            return self.client.get('/stock/manage/ping/')

    def test_manager_can_ping(self):
        resp = self._get_as(self.manager)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['data']['ok'])

    def test_floor_user_denied(self):
        resp = self._get_as(self.floor)
        self.assertEqual(resp.status_code, 403, resp.content)
