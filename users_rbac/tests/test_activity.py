import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, RequestFactory, TestCase

from locations.models import Location
from product.audit_log import capture_product_audit
from product.models import Category, Product, ProductAudit, ProductClass, Range, Unit
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

POOL = 'eu-west-2_testpool'
CLIENT_ID = 'client-id'
ISSUER = f'https://cognito-idp.eu-west-2.amazonaws.com/{POOL}'
ENV = {
    'COGNITO_USER_POOL_ID': POOL,
    'COGNITO_CLIENT_ID': CLIENT_ID,
    'COGNITO_REGION': 'eu-west-2',
}


def _rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key, *, sub: str):
    payload = {
        'sub': sub,
        'iss': ISSUER,
        'token_use': 'id',
        'aud': CLIENT_ID,
        'exp': int(time.time()) + 3600,
    }
    return jwt.encode(payload, private_key, algorithm='RS256', headers={'kid': 'test-kid'})


class ActivityAndGateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.private_key, self.public_key = _rsa_keypair()
        self.env = patch.dict('os.environ', ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.key_patch = patch(
            'users_rbac.auth._get_signing_key',
            return_value=self.public_key,
        )
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

        self.admin = RbacUser.objects.create(
            cognito_sub='sub-jane',
            username='jane.admin',
            display_name='Jane',
        )
        UserDepartment.objects.create(user=self.admin, department=Department.ADMIN)
        AdminAccess.objects.create(user=self.admin, area=AdminArea.TECHNICAL)
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )
        UserDepartment.objects.create(user=self.floor, department=Department.PRODUCTION)
        ProductionAccess.objects.create(user=self.floor, area=ProductionArea.LOW_RISK)
        self.admin_auth = f'Bearer {_token(self.private_key, sub="sub-jane")}'
        self.floor_auth = f'Bearer {_token(self.private_key, sub="sub-amit")}'

    def test_receipt_denied_without_warehouse_grant(self):
        response = self.client.post(
            '/stock/receipt/',
            data=json.dumps({'idempotency_key': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 403)
        event = RbacAuditEvent.objects.get(action=RbacAuditAction.AUTH_ACCESS_DENIED)
        self.assertEqual(event.actor_username, 'amit01')
        self.assertEqual(event.detail_json['required']['warehouse'], 'any')

    def test_receipt_legacy_unauthenticated_still_reaches_view(self):
        response = self.client.post(
            '/stock/receipt/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_warehouse_grant_passes_gate(self):
        WarehouseAccess.objects.create(
            user=self.floor,
            unit=WarehouseUnit.UNIT_1,
            can_goods_in=True,
            goods_in_today=True,
        )
        response = self.client.post(
            '/stock/receipt/',
            data=json.dumps({}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            RbacAuditEvent.objects.filter(
                action=RbacAuditAction.AUTH_ACCESS_DENIED,
            ).exists()
        )

    def test_product_audit_stamps_verified_user(self):
        Location.objects.create(id=1, name='Src', visible=True)
        Location.objects.create(id=2, name='Dst', visible=True)
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        product = Product.objects.create(
            name='Samosa',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        request = self.factory.post(
            f'/product/{product.id}/',
            REMOTE_ADDR='10.0.1.22',
            HTTP_AUTHORIZATION=self.floor_auth,
            HTTP_X_FORWARDED_FOR='10.0.1.22',
        )
        capture_product_audit(
            request,
            product_id=product.id,
            entity='product',
            action='update',
            before_data={'name': 'A'},
            after_data={'name': 'B'},
        )
        row = ProductAudit.objects.get(product_id=product.id)
        self.assertEqual(row.actor_sub, 'sub-amit')
        self.assertEqual(row.lan_username, 'Amit')
        self.assertEqual(row.source_workstation_ip, '10.0.1.22')

    def test_user_activity_merges_rbac_and_product(self):
        Location.objects.create(id=1, name='Src', visible=True)
        Location.objects.create(id=2, name='Dst', visible=True)
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        product = Product.objects.create(
            name='Samosa',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        ProductAudit.objects.create(
            product_id=product.id,
            actor_sub='sub-amit',
            lan_username='Amit',
            timeline_events=[
                {
                    'at': '2026-08-09T08:12:00+00:00',
                    'entity': 'product',
                    'action': 'update',
                    'actor_sub': 'sub-amit',
                    'actor_name': 'Amit',
                    'request_path': '/product/1/',
                    'source_workstation_ip': '10.0.1.22',
                }
            ],
        )
        RbacAuditEvent.objects.create(
            action=RbacAuditAction.AUTH_LOGIN_SUCCESS,
            actor_sub='sub-amit',
            actor_username='amit01',
            target_user=self.floor,
            target_username='amit01',
            source_ip='10.0.1.22',
        )
        response = self.client.get(
            f'/auth/users/{self.floor.id}/activity/',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        sources = {item['source'] for item in data['items']}
        self.assertIn('rbac', sources)
        self.assertIn('product', sources)
        self.assertGreaterEqual(data['count'], 2)

        forbidden = self.client.get(
            f'/auth/users/{self.floor.id}/activity/',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(forbidden.status_code, 403)
