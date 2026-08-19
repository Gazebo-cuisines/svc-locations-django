import json
import time
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
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

POOL = 'eu-west-2_testpool'
CLIENT_ID = 'client-id'
ISSUER = f'https://cognito-idp.eu-west-2.amazonaws.com/{POOL}'
ENV = {
    'COGNITO_USER_POOL_ID': POOL,
    'COGNITO_CLIENT_ID': CLIENT_ID,
    'COGNITO_REGION': 'eu-west-2',
    'COGNITO_CLIENT_SECRET': 'client-secret',
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


def _cognito_user(sub: str) -> dict:
    return {'User': {'Attributes': [{'Name': 'sub', 'Value': sub}]}}


class UserApiTests(TestCase):
    def setUp(self):
        self.client = Client()
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
        self.admin_auth = f'Bearer {_token(self.private_key, sub="sub-jane")}'
        self.floor_auth = f'Bearer {_token(self.private_key, sub="sub-amit")}'

    def test_me_returns_caller_grants(self):
        response = self.client.get('/auth/me/', HTTP_AUTHORIZATION=self.floor_auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['username'], 'amit01')
        self.assertEqual(data['departments'], ['production'])
        self.assertFalse(data['is_admin'])

    def test_me_warehouse_includes_location_id(self):
        UserDepartment.objects.filter(user=self.floor).delete()
        UserDepartment.objects.create(user=self.floor, department=Department.WAREHOUSE)
        WarehouseAccess.objects.create(
            user=self.floor,
            unit=WarehouseUnit.UNIT_2,
            can_goods_in=True,
            goods_in_previous=True,
            goods_in_today=True,
        )
        response = self.client.get('/auth/me/', HTTP_AUTHORIZATION=self.floor_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['data']['warehouse'],
            [
                {
                    'unit': 'unit_2',
                    'actions': ['goods_in'],
                    'goods_in_periods': ['previous', 'today'],
                    'location_id': 8,
                }
            ],
        )

    def test_floor_cannot_create_user(self):
        response = self.client.post(
            '/auth/users/',
            data=json.dumps({'username': 'bob01', 'password': 'Secret123!'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_creates_user_with_grants(self):
        mock_client = MagicMock()
        mock_client.admin_create_user.return_value = _cognito_user('sub-bob')
        with patch('users_rbac.services._client', return_value=mock_client):
            response = self.client.post(
                '/auth/users/',
                data=json.dumps(
                    {
                        'username': 'bob01',
                        'password': 'Secret123!',
                        'display_name': 'Bob',
                        'departments': ['production'],
                        'production_areas': ['low_risk'],
                    }
                ),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()['data']
        self.assertEqual(data['username'], 'bob01')
        self.assertEqual(data['production_areas'], ['low_risk'])
        self.assertTrue(RbacUser.objects.filter(username='bob01', cognito_sub='sub-bob').exists())

    def test_put_grants_and_reject_orphan_areas(self):
        response = self.client.put(
            f'/auth/users/{self.floor.id}/grants/',
            data=json.dumps(
                {
                    'departments': ['production'],
                    'production_areas': ['low_risk', 'dispatch'],
                    'warehouse': [],
                    'admin_areas': [],
                }
            ),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            set(response.json()['data']['production_areas']),
            {'low_risk', 'dispatch'},
        )

        bad = self.client.put(
            f'/auth/users/{self.floor.id}/grants/',
            data=json.dumps(
                {
                    'departments': [],
                    'production_areas': ['high_risk'],
                    'warehouse': [],
                    'admin_areas': [],
                }
            ),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(bad.status_code, 400)

    def test_patch_disable_and_reset_password(self):
        mock_client = MagicMock()
        with patch('users_rbac.services._client', return_value=mock_client):
            disable = self.client.patch(
                f'/auth/users/{self.floor.id}/',
                data=json.dumps({'is_active': False, 'display_name': 'Amit Floor'}),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
            reset = self.client.post(
                f'/auth/users/{self.floor.id}/reset-password/',
                data=json.dumps({'password': 'NewSecret123!'}),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
        self.assertEqual(disable.status_code, 200)
        self.assertFalse(disable.json()['data']['is_active'])
        self.assertEqual(disable.json()['data']['display_name'], 'Amit Floor')
        mock_client.admin_disable_user.assert_called_once()
        self.assertEqual(reset.status_code, 200)
        mock_client.admin_set_user_password.assert_called_once()

    def test_list_and_detail(self):
        listing = self.client.get(
            '/auth/users/?department=admin',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(listing.status_code, 200)
        usernames = {row['username'] for row in listing.json()['data']}
        self.assertEqual(usernames, {'jane.admin'})

        detail = self.client.get(
            f'/auth/users/{self.admin.id}/',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn('technical', detail.json()['data']['admin_areas'])

        missing = self.client.get('/auth/users/9999/', HTTP_AUTHORIZATION=self.admin_auth)
        self.assertEqual(missing.status_code, 404)
