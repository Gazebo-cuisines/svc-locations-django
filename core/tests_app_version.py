from django.test import Client, TestCase
from django.test.utils import override_settings


TOKEN = 'ci-app-version-token'


class AppVersionTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_android_public_no_auth(self):
        res = self.client.get('/app/version/?platform=android')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['status'], 'success')
        self.assertEqual(body['message'], 'ok')
        self.assertEqual(body['data']['min_version'], '1.0.1')
        self.assertEqual(body['data']['latest_version'], '1.0.1')
        self.assertEqual(
            body['data']['message'],
            'Hand this device to IT to install the update.',
        )

    @override_settings(APP_MIN_VERSION_ANDROID='1.0.2', APP_LATEST_VERSION_ANDROID='1.0.3')
    def test_env_fallback_when_no_row(self):
        res = self.client.get('/app/version/?platform=android')
        self.assertEqual(res.json()['data']['min_version'], '1.0.2')
        self.assertEqual(res.json()['data']['latest_version'], '1.0.3')

    def test_unknown_platform(self):
        res = self.client.get('/app/version/?platform=ios')
        self.assertEqual(res.status_code, 400)

    @override_settings(APP_VERSION_API_TOKEN='')
    def test_put_requires_token(self):
        res = self.client.put(
            '/app/version/',
            data='{"platform":"android","min_version":"1.0.1"}',
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 401)

    @override_settings(APP_VERSION_API_TOKEN=TOKEN)
    def test_put_rejects_user_jwt(self):
        res = self.client.put(
            '/app/version/',
            data='{"platform":"android","min_version":"9.9.9"}',
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer not-the-ci-token',
        )
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.client.get('/app/version/?platform=android').json()['data']['min_version'], '1.0.1')

    @override_settings(APP_VERSION_API_TOKEN=TOKEN)
    def test_put_sets_min_version(self):
        res = self.client.put(
            '/app/version/',
            data='{"platform":"android","min_version":"1.0.1","latest_version":"1.0.1"}',
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['data']['min_version'], '1.0.1')
        got = self.client.get('/app/version/?platform=android')
        self.assertEqual(got.json()['data']['min_version'], '1.0.1')
        self.assertEqual(got.json()['data']['latest_version'], '1.0.1')
