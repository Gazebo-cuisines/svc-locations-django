import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.http import JsonResponse
from django.test import RequestFactory, TestCase

from users_rbac.auth import client_ip, require_auth
from users_rbac.models import RbacUser

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


def _token(private_key, *, sub='sub-amit', exp_offset=3600, token_use='id', extra=None):
    payload = {
        'sub': sub,
        'iss': ISSUER,
        'token_use': token_use,
        'exp': int(time.time()) + exp_offset,
        'cognito:username': 'amit01',
    }
    if token_use == 'id':
        payload['aud'] = CLIENT_ID
    else:
        payload['client_id'] = CLIENT_ID
    if extra:
        payload.update(extra)
    return jwt.encode(payload, private_key, algorithm='RS256', headers={'kid': 'test-kid'})


@require_auth
def _ping(request):
    return JsonResponse(
        {
            'username': request.rbac_user.username,
            'ip': request.client_ip,
        }
    )


class RequireAuthTests(TestCase):
    def setUp(self):
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
        self.user = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )

    def _auth_request(self, token, **headers):
        return self.factory.get(
            '/auth/ping/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
            **headers,
        )

    def test_valid_token_attaches_user_and_xff_ip(self):
        token = _token(self.private_key)
        request = self._auth_request(
            token,
            HTTP_X_FORWARDED_FOR='10.0.1.22, 10.0.0.1',
            HTTP_USER_AGENT='ShopFloor/1',
        )
        response = _ping(request)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body['username'], 'amit01')
        self.assertEqual(body['ip'], '10.0.1.22')
        self.assertEqual(request.rbac_user.id, self.user.id)
        self.assertEqual(request.cognito_claims['sub'], 'sub-amit')

    def test_missing_bearer_is_401(self):
        request = self.factory.get('/auth/ping/')
        response = _ping(request)
        self.assertEqual(response.status_code, 401)

    def test_unsigned_token_is_401(self):
        request = self._auth_request('not-a-jwt')
        response = _ping(request)
        self.assertEqual(response.status_code, 401)

    def test_expired_token_is_401(self):
        token = _token(self.private_key, exp_offset=-60)
        request = self._auth_request(token)
        response = _ping(request)
        self.assertEqual(response.status_code, 401)

    def test_unknown_sub_is_401(self):
        token = _token(self.private_key, sub='sub-unknown')
        request = self._auth_request(token)
        response = _ping(request)
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_is_403(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        token = _token(self.private_key)
        request = self._auth_request(token)
        response = _ping(request)
        self.assertEqual(response.status_code, 403)

    def test_client_ip_falls_back_to_remote_addr(self):
        request = self.factory.get('/auth/ping/', REMOTE_ADDR='192.168.1.9')
        self.assertEqual(client_ip(request), '192.168.1.9')
