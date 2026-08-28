import json
import time
from datetime import timedelta
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.utils import timezone

from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
)
from users_rbac.presence import STAMP_AFTER

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


class PresenceAndMaintenanceTests(TestCase):
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
        self.idle = RbacUser.objects.create(
            cognito_sub='sub-idle',
            username='idle01',
            display_name='Idle',
        )
        self.admin_auth = f'Bearer {_token(self.private_key, sub="sub-jane")}'
        self.floor_auth = f'Bearer {_token(self.private_key, sub="sub-amit")}'

    def test_auth_call_stamps_ip_and_lists_online(self):
        resp = self.client.get(
            '/auth/me/',
            HTTP_AUTHORIZATION=self.floor_auth,
            HTTP_X_FORWARDED_FOR='10.0.1.22',
            HTTP_USER_AGENT='ShopFloor/1',
        )
        self.assertEqual(resp.status_code, 200)
        self.floor.refresh_from_db()
        self.assertEqual(self.floor.last_ip, '10.0.1.22')
        self.assertEqual(self.floor.last_user_agent, 'ShopFloor/1')
        self.assertIsNotNone(self.floor.last_seen_at)
        self.assertEqual(resp.json()['data']['maintenance']['is_active'], False)

        listed = self.client.get(
            '/auth/presence/',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(listed.status_code, 200)
        by_user = {row['username']: row for row in listed.json()['data']}
        self.assertIn('amit01', by_user)
        self.assertEqual(by_user['amit01']['presence'], 'online')
        self.assertEqual(by_user['amit01']['last_ip'], '10.0.1.22')

    def test_stamp_throttled_within_60s(self):
        self.client.get(
            '/auth/me/',
            HTTP_AUTHORIZATION=self.floor_auth,
            HTTP_X_FORWARDED_FOR='10.0.1.22',
        )
        self.floor.refresh_from_db()
        first = self.floor.last_seen_at
        self.client.get(
            '/auth/me/',
            HTTP_AUTHORIZATION=self.floor_auth,
            HTTP_X_FORWARDED_FOR='10.0.1.99',
        )
        self.floor.refresh_from_db()
        self.assertEqual(self.floor.last_seen_at, first)
        self.assertEqual(self.floor.last_ip, '10.0.1.22')
        self.assertLess(STAMP_AFTER, timedelta(minutes=2))

    def test_stale_user_hidden_unless_all(self):
        self.idle.last_seen_at = timezone.now() - timedelta(minutes=11)
        self.idle.last_ip = '10.0.0.9'
        self.idle.save(update_fields=['last_seen_at', 'last_ip'])
        hidden = self.client.get(
            '/auth/presence/',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        hidden_names = {row['username'] for row in hidden.json()['data']}
        self.assertNotIn('idle01', hidden_names)
        shown = self.client.get(
            '/auth/presence/?all=1',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        shown_names = {row['username']: row for row in shown.json()['data']}
        self.assertIn('idle01', shown_names)
        self.assertEqual(shown_names['idle01']['presence'], 'offline')

    def test_floor_cannot_list_presence(self):
        resp = self.client.get(
            '/auth/presence/',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(resp.status_code, 403)

    def test_maintenance_round_trip_and_clear(self):
        put = self.client.put(
            '/ops/maintenance/',
            data=json.dumps({
                'is_active': True,
                'message': 'Please allow us 10 minutes for maintenance.',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(put.status_code, 200, put.content)
        me = self.client.get('/auth/me/', HTTP_AUTHORIZATION=self.floor_auth)
        notice = me.json()['data']['maintenance']
        self.assertTrue(notice['is_active'])
        self.assertIn('10 minutes', notice['message'])

        clear = self.client.put(
            '/ops/maintenance/',
            data=json.dumps({'is_active': False}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(clear.status_code, 200)
        me2 = self.client.get('/auth/me/', HTTP_AUTHORIZATION=self.floor_auth)
        self.assertFalse(me2.json()['data']['maintenance']['is_active'])
        self.assertIsNone(me2.json()['data']['maintenance']['message'])

    def test_floor_cannot_set_maintenance(self):
        resp = self.client.put(
            '/ops/maintenance/',
            data=json.dumps({'is_active': True, 'message': 'down'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(resp.status_code, 403)

    def test_writes_locked_with_423_gets_still_work(self):
        self.client.put(
            '/ops/maintenance/',
            data=json.dumps({
                'is_active': True,
                'message': 'Please allow us 10 minutes for maintenance.',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        blocked = self.client.patch(
            f'/auth/users/{self.floor.id}/',
            data=json.dumps({'display_name': 'Nope'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(blocked.status_code, 423, blocked.content)
        payload = blocked.json()
        self.assertEqual(payload['status'], 'error')
        self.assertTrue(payload['data']['maintenance']['is_active'])
        me = self.client.get('/auth/me/', HTTP_AUTHORIZATION=self.floor_auth)
        self.assertEqual(me.status_code, 200)
        self.floor.refresh_from_db()
        self.assertEqual(self.floor.display_name, 'Amit')

    @override_settings(MAINTENANCE_WEBHOOK_URL='https://hooks.example/slack')
    def test_webhook_fires_once_on_activate(self):
        with patch('core.maintenance.urllib.request.urlopen') as mock_open:
            self.client.put(
                '/ops/maintenance/',
                data=json.dumps({
                    'is_active': True,
                    'message': 'Please allow us 10 minutes for maintenance.',
                }),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
            self.client.put(
                '/ops/maintenance/',
                data=json.dumps({
                    'is_active': True,
                    'message': 'Still down.',
                }),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
            self.client.put(
                '/ops/maintenance/',
                data=json.dumps({'is_active': False}),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.admin_auth,
            )
        self.assertEqual(mock_open.call_count, 1)
