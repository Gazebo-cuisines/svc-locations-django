"""Admin CRUD for goods-in check templates."""

from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import (
    GoodsInCheckTemplate,
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderStatus,
)
from purchasing.services.check_templates import (
    CheckTemplateError,
    add_item,
    create_template,
    list_templates,
    update_item,
    update_template,
)
from users_rbac.models import AdminAccess, AdminArea, Department, RbacUser, UserDepartment


class CheckTemplateServiceTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()

    def _ambient_header(self):
        return GoodsInCheckTemplate.objects.get(
            goods_in_type='raw_material',
            storage_regime='ambient',
            scope='header',
            version=1,
        )

    def test_list_latest_ambient_has_no_vehicle_temp(self):
        rows = list_templates(filters={
            'goods_in_type': 'raw_material',
            'scope': 'header',
            'latest': 'true',
        })
        ambient = next(r for r in rows if r['storage_regime'] == 'ambient')
        codes = {item['code'] for item in ambient['items']}
        self.assertNotIn('vehicle_temperature', codes)
        self.assertFalse(ambient['in_use'])

    def test_edit_then_lock_after_submitted_qc(self):
        template = self._ambient_header()
        item = template.items.get(code='vehicle_clean_fb_pest_odour')
        updated = update_item(template.id, item.id, {'label': 'Vehicle clean?'})
        self.assertEqual(updated['label'], 'Vehicle clean?')

        wh = Location.objects.create(id=61, name='CT WH', visible=True)
        supplier = Location.objects.create(id=62, name='CT Sup', visible=True)
        po = PurchaseOrder.objects.create(
            number=f'CT-{uuid4().hex[:6]}',
            supplier=supplier,
            ship_to_location=wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
        )
        PurchaseOrderDelivery.objects.create(
            purchase_order=po,
            header_template_id=template.id,
            checked_at=timezone.now(),
        )
        with self.assertRaises(CheckTemplateError) as ctx:
            add_item(template.id, {
                'code': 'seal_ok',
                'label': 'Seal OK',
                'input_type': 'bool',
            })
        self.assertEqual(ctx.exception.status_code, 409)

        clone = create_template({
            'clone_from_id': template.id,
            'reason_for_change': 'Added seal check',
        })
        self.assertEqual(clone['version'], 2)
        self.assertTrue(clone['is_active'])
        self._ambient_header().refresh_from_db()
        self.assertFalse(GoodsInCheckTemplate.objects.get(pk=template.id).is_active)
        add_item(clone['id'], {
            'code': 'seal_ok',
            'label': 'Seal OK',
            'input_type': 'bool',
            'required': True,
            'sort_order': 80,
        })
        self.assertTrue(
            any(i['code'] == 'seal_ok' for i in list_templates(filters={
                'goods_in_type': 'raw_material',
                'storage_regime': 'ambient',
                'scope': 'header',
                'latest': 'true',
            })[0]['items']),
        )

    def test_cannot_change_key_fields(self):
        template = self._ambient_header()
        with self.assertRaises(CheckTemplateError):
            update_template(template.id, {'scope': 'line'})


class CheckTemplateApiTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        self.client = Client()
        self.admin = RbacUser.objects.create(
            cognito_sub='sub-qa',
            username='qa.admin',
            display_name='QA',
        )
        UserDepartment.objects.create(user=self.admin, department=Department.ADMIN)
        AdminAccess.objects.create(user=self.admin, area=AdminArea.TECHNICAL)
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-floor',
            username='floor01',
            display_name='Floor',
        )

    def _attach(self, user):
        def fake(request, **kwargs):
            request.rbac_user = user
            return None

        return patch('users_rbac.auth.attach_user', side_effect=fake)

    def test_admin_list_and_floor_denied(self):
        with self._attach(self.admin):
            resp = self.client.get('/purchasing/check-templates/?latest=true')
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.json()['data']['count'], 1)

        with self._attach(self.floor):
            denied = self.client.get('/purchasing/check-templates/')
        self.assertEqual(denied.status_code, 403)
