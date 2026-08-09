import json
import time
from unittest.mock import MagicMock, patch

import jwt
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, TestCase

from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacAuditAction,
    RbacAuditEvent,
    RbacUser,
    UserDepartment,
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


class AuditApiTests(TestCase):
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

    def test_login_success_and_failure_audit(self):
        mock_client = MagicMock()
        mock_client.initiate_auth.return_value = {
            'AuthenticationResult': {
                'AccessToken': 'a',
                'IdToken': 'i',
                'RefreshToken': 'r',
                'ExpiresIn': 3600,
                'TokenType': 'Bearer',
            }
        }
        with patch('users_rbac.services._client', return_value=mock_client):
            ok = self.client.post(
                '/auth/login/',
                data=json.dumps({'username': 'amit01', 'password': 'Secret123!'}),
                content_type='application/json',
                HTTP_X_FORWARDED_FOR='10.0.1.22',
            )
        self.assertEqual(ok.status_code, 200)
        success = RbacAuditEvent.objects.get(action=RbacAuditAction.AUTH_LOGIN_SUCCESS)
        self.assertEqual(success.actor_username, 'amit01')
        self.assertEqual(success.source_ip, '10.0.1.22')

        mock_client.initiate_auth.side_effect = ClientError(
            {'Error': {'Code': 'NotAuthorizedException', 'Message': 'bad'}},
            'op',
        )
        fail = self.client.post(
            '/auth/login/',
            data=json.dumps({'username': 'amit01', 'password': 'wrong'}),
            content_type='application/json',
        )
        self.assertEqual(fail.status_code, 401)
        self.assertTrue(
            RbacAuditEvent.objects.filter(action=RbacAuditAction.AUTH_LOGIN_FAILURE).exists()
        )

    def test_grants_replace_and_create_audit(self):
        mock_client = MagicMock()
        mock_client.admin_create_user.return_value = {
            'User': {'Attributes': [{'Name': 'sub', 'Value': 'sub-bob'}]}
        }
        with patch('users_rbac.services._client', return_value=mock_client):
            created = self.client.post(
                '/auth/users/',
                data=json.dumps(
                    {
                        'username': 'bob01',
                        'password': 'Secret123!',
                        'departments': ['production'],
                        'production_areas': ['low_risk'],
                    }
                ),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
        self.assertEqual(created.status_code, 201, created.content)
        create_event = RbacAuditEvent.objects.get(action=RbacAuditAction.USER_CREATED)
        self.assertEqual(create_event.actor_username, 'jane.admin')
        self.assertEqual(create_event.target_username, 'bob01')
        self.assertIn('low_risk', create_event.after_json['production_areas'])

        grants = self.client.put(
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
        self.assertEqual(grants.status_code, 200)
        replaced = RbacAuditEvent.objects.get(action=RbacAuditAction.GRANTS_REPLACED)
        self.assertEqual(replaced.actor_username, 'jane.admin')
        self.assertEqual(replaced.target_username, 'amit01')
        self.assertEqual(set(replaced.after_json['production_areas']), {'low_risk', 'dispatch'})

    def test_audit_list_and_user_timeline(self):
        RbacAuditEvent.objects.create(
            action=RbacAuditAction.AUTH_LOGIN_SUCCESS,
            actor_sub='sub-amit',
            actor_username='amit01',
            target_user=self.floor,
            target_username='amit01',
            source_ip='10.0.1.22',
        )
        listing = self.client.get(
            '/auth/audit/?actor=amit01',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()['data']['count'], 1)
        self.assertEqual(listing.json()['data']['items'][0]['action'], 'auth.login_success')

        forbidden = self.client.get('/auth/audit/', HTTP_AUTHORIZATION=self.floor_auth)
        self.assertEqual(forbidden.status_code, 403)

        timeline = self.client.get(
            f'/auth/users/{self.floor.id}/audit/?as=both',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(timeline.status_code, 200)
        self.assertGreaterEqual(timeline.json()['data']['count'], 1)
