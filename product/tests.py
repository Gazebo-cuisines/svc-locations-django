import base64
import json
from decimal import Decimal

from django.test import TestCase

from locations.models import Location
from planning.models import Resource
from product.models import Category, ProductClass, Unit


class ProductApiTests(TestCase):
    def setUp(self):
        self._seed_lookups()
        self._seed_locations()
        self.auth_header = self._build_auth_header(
            sub='abc-user-sub',
            name='Utsav Gohel',
            email='utsav@example.com',
        )
        self.product_id = self._create_product('Product 101')

    def _seed_lookups(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Unit.objects.create(id=1, name='Each')

    def _seed_locations(self):
        Location.objects.create(id=1, name='Source', visible=True)
        Location.objects.create(id=2, name='Destination', visible=True)

    def _core_body(self, name: str, **overrides):
        payload = {
            'name': name,
            'product_class_id': 1,
            'category_id': 1,
            'unit_id': 1,
            'source_container_id': 1,
            'destination_container_id': 2,
            'storage_regime': 'frozen',
        }
        payload.update(overrides)
        return payload

    def _post(self, path: str, payload: dict):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )

    def _seed_product(self, name: str, **overrides):
        return self._post('/product/', self._core_body(name, **overrides))

    def _create_product(self, name: str, **overrides) -> int:
        resp = self._seed_product(name, **overrides)
        assert resp.status_code == 201, resp.content
        return resp.json()['data']['ref']

    def _build_auth_header(self, *, sub: str, name: str, email: str) -> str:
        header = {'alg': 'none', 'typ': 'JWT'}
        payload = {'sub': sub, 'name': name, 'email': email}

        def _enc(data):
            raw = json.dumps(data).encode('utf-8')
            return base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')

        return f'Bearer {_enc(header)}.{_enc(payload)}.sig'

    def test_product_list_filters_by_containers(self):
        Location.objects.create(id=3, name='Other Dest', visible=True)
        other_id = self._create_product('Other Flow', destination_container_id=3)

        all_resp = self.client.get('/product/')
        self.assertEqual(all_resp.status_code, 200)
        all_rows = all_resp.json()['data']
        self.assertTrue(all(r.get('source_container_id') is not None for r in all_rows))
        self.assertIn('shelf_life_days', all_rows[0])

        pair_resp = self.client.get(
            '/product/?source_container_id=1&destination_container_id=2',
        )
        self.assertEqual(pair_resp.status_code, 200)
        pair_ids = {r['id'] for r in pair_resp.json()['data']}
        self.assertIn(self.product_id, pair_ids)
        self.assertNotIn(other_id, pair_ids)

        bad = self.client.get('/product/?source_container_id=abc')
        self.assertEqual(bad.status_code, 400)

    def test_product_create_update_delete_and_timeline(self):
        create_resp = self._seed_product('Product 102')
        self.assertEqual(create_resp.status_code, 201)
        product_id = create_resp.json()['data']['ref']

        update_resp = self.client.patch(
            f'/product/{product_id}/',
            data=json.dumps({
                'name': 'Product 102 Updated',
                'is_active': False,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertTrue(update_resp.json()['data']['is_active'])

        delete_resp = self.client.delete(
            f'/product/{product_id}/',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(delete_resp.status_code, 200)

        timeline_resp = self.client.get(f'/product/{product_id}/timeline/')
        self.assertEqual(timeline_resp.status_code, 200)
        events = timeline_resp.json()['data']
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['action'], 'delete')
        self.assertEqual(events[1]['action'], 'update')
        self.assertEqual(events[2]['action'], 'create')
        self.assertEqual(events[1]['actor_name'], 'Utsav Gohel')

    def test_ops_satellite_endpoints_create_rows(self):
        tray_id = self._create_product('Tray 8x4')
        Resource.objects.create(id=3, code='LINE3', name='Line 3', location_id=1)

        base = f'/product/{self.product_id}'
        cases = [
            (f'{base}/costing/', {'unit_cost': '1.23', 'unit_price': '2.50'}),
            (f'{base}/shelf-life/', {'shelf_life_days': 5, 'force_use_by': True}),
            (f'{base}/stock-policy/', {'reorder_level': '10.0'}),
            (f'{base}/packaging/', {'pack_weight': '0.25', 'tray_id': tray_id}),
            (f'{base}/production/', {'avg_minutes': '25.0', 'default_resource_id': 3}),
            (f'{base}/yield/', {'yield_factor': '0.95', 'yield_factor_auto': '0.94'}),
        ]
        for path, payload in cases:
            resp = self.client.put(
                path,
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_AUTHORIZATION=self.auth_header,
            )
            self.assertIn(resp.status_code, (200, 201), msg=path)

            get_resp = self.client.get(path)
            self.assertEqual(get_resp.status_code, 200, msg=path)

    def test_label_mode_round_trips_and_rejects_unknown_value(self):
        detail = self.client.get(f'/product/{self.product_id}/').json()['data']
        self.assertEqual(detail['label_mode'], 'product')

        resp = self.client.patch(
            f'/product/{self.product_id}/',
            data=json.dumps({'label_mode': 'per_unit'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['label_mode'], 'per_unit')

        bad = self.client.patch(
            f'/product/{self.product_id}/',
            data=json.dumps({'label_mode': 'per-unit'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(bad.status_code, 400)

        batch_id = self._create_product('Batch Labelled', label_mode='batch')
        batch_detail = self.client.get(f'/product/{batch_id}/').json()['data']
        self.assertEqual(batch_detail['label_mode'], 'batch')

    def test_invalid_payload_returns_400(self):
        resp = self.client.put(
            f'/product/{self.product_id}/costing/',
            data=json.dumps({'unit_cost': 'bad-decimal'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(resp.status_code, 400)

    def test_sleeving_create_derives_case_size_and_sets_flags(self):
        Unit.objects.create(id=2, name='GM')
        resp = self._post('/product/sleeving/', self._core_body(
            'Sleeve 400',
            items_per_unit=4,
            unitary_weight=400,
            case_size_unit_id=2,
            shelf_life_days=7,
        ))
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(Decimal(data['packaging']['pack_weight']), Decimal('1600'))
        self.assertEqual(data['costing']['case_size_description'], '4 x 400 GM')
        self.assertEqual(data['shelf_life']['shelf_life_days'], 7)
        self.assertTrue(data['shelf_life']['force_production_date'])
        self.assertTrue(data['shelf_life']['force_use_by'])
        self.assertTrue(data['flags']['is_sales_item'])
        self.assertTrue(data['flags']['has_plan'])
        self.assertTrue(data['flags']['include_in_projections'])

    def test_high_risk_create_sets_gas_flush_and_unsold_flags(self):
        resp = self._post('/product/high-risk/', self._core_body(
            'High Risk Tray',
            pack_weight='1.25',
        ))
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(Decimal(data['packaging']['pack_weight']), Decimal('1.25'))
        self.assertTrue(data['packaging']['is_gas_flush'])
        self.assertIsNone(data['packaging']['tray_id'])
        self.assertIsNone(data['packaging']['container_vessel_id'])
        self.assertIsNone(data['shelf_life'])
        self.assertFalse(data['flags']['is_sales_item'])
        self.assertFalse(data['flags']['include_in_projections'])
