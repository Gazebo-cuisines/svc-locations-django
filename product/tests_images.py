from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from locations.models import Location
from product.models import Category, Product, ProductClass, ProductImage, Unit


def _jpeg(name='item.jpg'):
    buf = BytesIO()
    Image.new('RGB', (8, 8), (200, 40, 40)).save(buf, format='JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


class ProductImageApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Source', visible=True)
        Location.objects.create(id=2, name='Destination', visible=True)
        self.product = Product.objects.create(
            name='Potato',
            product_class_id=1,
            category_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        self.product_id = self.product.id
        self.auth_header = 'Bearer test'

    def _s3(self, s3_factory):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://s3.example/signed'
        s3_factory.return_value = s3
        return s3

    @patch('product.product_images._s3_client')
    def test_first_upload_is_main(self, s3_factory):
        self._s3(s3_factory)
        resp = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg()},
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()['data']
        self.assertTrue(data['is_main'])
        self.assertEqual(data['url'], 'https://s3.example/signed')
        self.assertEqual(data['product_id'], self.product_id)

    @patch('product.product_images._s3_client')
    def test_second_upload_not_main_until_patched(self, s3_factory):
        self._s3(s3_factory)
        first = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg('a.jpg')},
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        second = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg('b.jpg'), 'is_main': 'true'},
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        self.assertTrue(second['is_main'])
        listed = self.client.get(
            f'/product/{self.product_id}/images/',
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        self.assertEqual(listed['count'], 2)
        by_id = {row['id']: row for row in listed['results']}
        self.assertFalse(by_id[first['id']]['is_main'])
        self.assertTrue(by_id[second['id']]['is_main'])
        self.assertEqual(listed['results'][0]['id'], second['id'])

    @patch('product.product_images._s3_client')
    def test_patch_sort_and_reject_clear_main(self, s3_factory):
        self._s3(s3_factory)
        created = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg()},
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        resp = self.client.patch(
            f"/product/images/{created['id']}/",
            data='{"sort_order": 3}',
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['sort_order'], 3)
        cleared = self.client.patch(
            f"/product/images/{created['id']}/",
            data='{"is_main": false}',
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(cleared.status_code, 400)

    @patch('product.product_images._s3_client')
    def test_delete_main_promotes_next(self, s3_factory):
        self._s3(s3_factory)
        first = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg('a.jpg')},
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        second = self.client.post(
            f'/product/{self.product_id}/images/',
            {'file': _jpeg('b.jpg'), 'sort_order': '1'},
            HTTP_AUTHORIZATION=self.auth_header,
        ).json()['data']
        resp = self.client.delete(
            f"/product/images/{first['id']}/",
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        remaining = ProductImage.objects.get(pk=second['id'])
        self.assertTrue(remaining.is_main)

    def test_upload_without_file_is_400(self):
        resp = self.client.post(
            f'/product/{self.product_id}/images/',
            {},
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_product_not_found(self):
        resp = self.client.get('/product/999999/images/')
        self.assertEqual(resp.status_code, 404)
