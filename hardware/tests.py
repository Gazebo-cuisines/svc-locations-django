import json
import time
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase

from hardware.models import HardwareDevice, HardwareDeviceAction, HardwareDeviceEvent
from stock_ledger.views import _common_write_kwargs
from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
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
SERIAL = '26202524703110'


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


class HardwareApiTests(TestCase):
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
        UserDepartment.objects.create(user=self.floor, department=Department.WAREHOUSE)
        self.admin_auth = f'Bearer {_token(self.private_key, sub="sub-jane")}'
        self.floor_auth = f'Bearer {_token(self.private_key, sub="sub-amit")}'

    def _login(self, *, serial=None):
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
        headers = {'HTTP_AUTHORIZATION': self.floor_auth}
        if serial:
            headers['HTTP_X_DEVICE_SERIAL'] = serial
            headers['HTTP_X_DEVICE_NICKNAME'] = 'BSD01_Gazeboo_cloud'
        with patch('users_rbac.services._client', return_value=mock_client):
            return self.client.post(
                '/auth/login/',
                data=json.dumps({'username': 'amit01', 'password': 'Secret123!'}),
                content_type='application/json',
                **headers,
            )

    def test_pc_login_has_no_device(self):
        res = self._login()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(HardwareDevice.objects.count(), 0)
        self.assertEqual(HardwareDeviceEvent.objects.count(), 0)

    def test_gun_login_enrolls_and_usage(self):
        res = self._login(serial=SERIAL)
        self.assertEqual(res.status_code, 200)
        gun = HardwareDevice.objects.get(serial=SERIAL)
        self.assertEqual(gun.code, 'GUN-01')
        self.assertEqual(gun.nickname, 'BSD01_Gazeboo_cloud')
        self.assertEqual(gun.last_user_id, self.floor.id)
        usage = self.client.get(
            '/hardware/usage/?code=GUN-01',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(usage.status_code, 200)
        items = usage.json()['data']['items']
        self.assertTrue(any(row['action'] == HardwareDeviceAction.LOGIN for row in items))
        self.assertEqual(items[0]['username'], 'amit01')

    def test_pc_write_kwargs_omit_serial(self):
        request = RequestFactory().post('/stock/receipt/', data={}, content_type='application/json')
        kwargs = _common_write_kwargs(request, {})
        self.assertNotIn('device_serial', kwargs)

    def test_gun_write_kwargs_stamp_serial(self):
        request = RequestFactory().post(
            '/stock/receipt/',
            data={},
            content_type='application/json',
            HTTP_X_DEVICE_SERIAL=SERIAL,
        )
        kwargs = _common_write_kwargs(request, {})
        self.assertEqual(kwargs['device_serial'], SERIAL)
        self.assertEqual(HardwareDevice.objects.get(serial=SERIAL).code, 'GUN-01')

    def test_allocate_gun03(self):
        created = self.client.post(
            '/hardware/devices/',
            data=json.dumps({'code': 'GUN-03', 'nickname': 'Dock 3'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        patched = self.client.patch(
            '/hardware/devices/GUN-03/',
            data=json.dumps({'assigned_user_id': self.floor.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        data = patched.json()['data']
        self.assertEqual(data['code'], 'GUN-03')
        self.assertEqual(data['assigned_user_id'], self.floor.id)
        listed = self.client.get(
            '/hardware/devices/',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()['data'][0]['code'], 'GUN-03')
        forbidden = self.client.get(
            '/hardware/usage/',
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(forbidden.status_code, 403)

    @patch('hardware.media._s3_client')
    def test_device_feed_post(self, s3_factory):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://s3.example/gun.jpg'
        s3_factory.return_value = s3
        self.client.post(
            '/hardware/devices/',
            data=json.dumps({'code': 'GUN-01', 'nickname': 'BSD01'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        jpeg = SimpleUploadedFile(
            'gun.jpg', b'\xff\xd8\xfffakejpeg', content_type='image/jpeg',
        )
        created = self.client.post(
            '/hardware/devices/GUN-01/posts/',
            {'file': jpeg, 'caption': 'Docked at Unit 2'},
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(created.status_code, 201, created.content)
        post = created.json()['data']
        self.assertEqual(post['caption'], 'Docked at Unit 2')
        self.assertEqual(post['kind'], 'image')
        self.assertEqual(post['username'], 'amit01')
        self.assertEqual(post['media_url'], 'https://s3.example/gun.jpg')
        self.assertEqual(post['device_code'], 'GUN-01')
        listed = self.client.get(
            '/hardware/devices/',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        card = listed.json()['data'][0]
        self.assertEqual(card['cover_url'], 'https://s3.example/gun.jpg')
        self.assertEqual(card['post_count'], 1)
        feed = self.client.get(
            '/hardware/feed/?code=GUN-01',
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(feed.status_code, 200)
        self.assertEqual(feed.json()['data']['count'], 1)
        deleted = self.client.delete(
            f"/hardware/devices/GUN-01/posts/{post['id']}/",
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(deleted.status_code, 200)
