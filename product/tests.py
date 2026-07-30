import base64
import json

from django.test import TestCase

from locations.models import Location
from product.models import Category, ProductClass, Range, Unit


class ProductApiTests(TestCase):
    def setUp(self):
        self._seed_lookups()
        self._seed_locations()
        self._seed_product(101, 'Product 101')
        self.auth_header = self._build_auth_header(
            sub='abc-user-sub',
            name='Utsav Gohel',
            email='utsav@example.com',
        )

    def _seed_lookups(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')

    def _seed_locations(self):
        Location.objects.create(id=1, name='Source', visible=True)
        Location.objects.create(id=2, name='Destination', visible=True)

    def _seed_product(self, product_id: int, name: str):
        return self.client.post(
            '/product/',
            data=json.dumps({
                'id': product_id,
                'name': name,
                'product_class_id': 1,
                'category_id': 1,
                'range_id': 1,
                'unit_id': 1,
                'source_container_id': 1,
                'destination_container_id': 2,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )

    def _build_auth_header(self, *, sub: str, name: str, email: str) -> str:
        header = {'alg': 'none', 'typ': 'JWT'}
        payload = {'sub': sub, 'name': name, 'email': email}

        def _enc(data):
            raw = json.dumps(data).encode('utf-8')
            return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')

        return f'Bearer {_enc(header)}.{_enc(payload)}.sig'

    def test_product_create_update_delete_and_timeline(self):
        create_resp = self._seed_product(102, 'Product 102')
        self.assertEqual(create_resp.status_code, 201)

        update_resp = self.client.patch(
            '/product/102/',
            data=json.dumps({'name': 'Product 102 Updated'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(update_resp.status_code, 200)

        delete_resp = self.client.delete(
            '/product/102/',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(delete_resp.status_code, 200)

        timeline_resp = self.client.get('/product/102/timeline/')
        self.assertEqual(timeline_resp.status_code, 200)
        events = timeline_resp.json()['data']
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['action'], 'delete')
        self.assertEqual(events[1]['action'], 'update')
        self.assertEqual(events[2]['action'], 'create')
        self.assertEqual(events[1]['actor_name'], 'Utsav Gohel')

    def test_ops_satellite_endpoints_create_rows(self):
        cases = [
            ('/product/101/costing/', {'unit_cost': '1.23', 'unit_price': '2.50'}),
            ('/product/101/shelf-life/', {'shelf_life_days': 5, 'force_use_by': True}),
            ('/product/101/stock-policy/', {'reorder_level': '10.0'}),
            ('/product/101/packaging/', {'pack_weight': '0.25', 'tray_id': 101}),
            ('/product/101/production/', {'avg_minutes': '25.0', 'default_resource_id': 3}),
            ('/product/101/yield/', {'yield_factor': '0.95', 'yield_factor_auto': '0.94'}),
        ]
        for path, payload in cases:
            resp = self.client.put(
                path,
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.auth_header,
            )
            self.assertIn(resp.status_code, (200, 201))

            get_resp = self.client.get(path)
            self.assertEqual(get_resp.status_code, 200)

    def test_invalid_payload_returns_400(self):
        resp = self.client.put(
            '/product/101/costing/',
            data=json.dumps({'unit_cost': 'bad-decimal'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)
