from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from users_rbac.models import AdminAccess, AdminArea, Department, RbacUser, UserDepartment
from users_rbac.tests.test_users import ENV, _token, _rsa_keypair


class UserPhotoTests(TestCase):
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
        self.admin_auth = f'Bearer {_token(self.private_key, sub="sub-jane")}'
        self.floor_auth = f'Bearer {_token(self.private_key, sub="sub-amit")}'

    def _jpeg(self, name='face.jpg'):
        buf = BytesIO()
        Image.new('RGB', (8, 8), (20, 80, 180)).save(buf, format='JPEG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')

    @patch('users_rbac.photos.presigned_get', return_value='https://s3.example/signed')
    @patch('users_rbac.photos._s3_client')
    def test_me_photo_upload(self, s3_factory, _presign):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://s3.example/signed'
        s3_factory.return_value = s3

        response = self.client.post(
            '/auth/me/photo/',
            {'file': self._jpeg()},
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 200)
        self.floor.refresh_from_db()
        self.assertTrue(self.floor.photo_key.startswith('User-profile/sub-amit/photo-'))
        self.assertTrue(self.floor.photo_key.endswith('.webp'))
        s3.put_object.assert_called_once()
        put_kwargs = s3.put_object.call_args.kwargs
        self.assertEqual(put_kwargs['Bucket'], 'gazebo-media-files')
        self.assertEqual(put_kwargs['ServerSideEncryption'], 'AES256')
        self.assertEqual(response.json()['data']['photo_url'], 'https://s3.example/signed')

    @patch('users_rbac.photos.presigned_get', return_value='https://s3.example/signed')
    @patch('users_rbac.photos._s3_client')
    def test_admin_can_set_user_photo(self, s3_factory, _presign):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://s3.example/signed'
        s3_factory.return_value = s3

        response = self.client.post(
            f'/auth/users/{self.floor.id}/photo/',
            {'file': self._jpeg()},
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(response.status_code, 200)
        self.floor.refresh_from_db()
        self.assertIn('User-profile/sub-amit/', self.floor.photo_key)

    def test_floor_cannot_set_other_photo(self):
        response = self.client.post(
            f'/auth/users/{self.admin.id}/photo/',
            {'file': self._jpeg()},
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 403)

    def test_rejects_non_image(self):
        bad = SimpleUploadedFile('x.txt', b'hello', content_type='text/plain')
        response = self.client.post(
            '/auth/me/photo/',
            {'file': bad},
            HTTP_AUTHORIZATION=self.floor_auth,
        )
        self.assertEqual(response.status_code, 400)
