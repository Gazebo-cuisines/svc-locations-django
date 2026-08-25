import json
from decimal import Decimal

from django.test import TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import Category, Product, ProductClass, ProductSupplier, Unit


class SupplierProductCostMoqTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Raw')
        Category.objects.create(id=1, name='Ingredients')
        self.kg = Unit.objects.create(id=1, name='Kg')
        self.litre = Unit.objects.create(id=2, name='Litre')
        self.case = Unit.objects.create(id=3, name='Case')
        self.drum = Unit.objects.create(id=4, name='Drum')
        self.wh = Location.objects.create(id=1, name='WH', visible=True)
        self.supplier = Location.objects.create(id=2, name='Starch Co', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.starch = Product.objects.create(
            name='NATIVE POTATO STARCH',
            product_class_id=1,
            category_id=1,
            unit=self.kg,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.oil = Product.objects.create(
            name='SUNFLOWER OIL',
            product_class_id=1,
            category_id=1,
            unit=self.litre,
            source_container=self.wh,
            destination_container=self.wh,
        )

    def _create_mapping(self, **overrides):
        payload = {
            'product': self.starch,
            'supplier': self.supplier,
            'supplier_code': '50100-POT-STARCH',
            'supplier_product_name': 'NATIVE POTATO STARCH 25KG',
            'cost': Decimal('28'),
            'outer_qty': Decimal('1'),
            'outer_unit': self.case,
            'inner_qty': Decimal('25'),
            'inner_unit': self.kg,
        }
        payload.update(overrides)
        return ProductSupplier.objects.create(**payload)

    def test_cost_per_unit_kg(self):
        row = self._create_mapping()
        resp = self.client.get(f'/product/{self.starch.id}/suppliers/{row.id}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['cost'], '28.000000')
        self.assertEqual(data['multiplier'], '25.000000')
        self.assertEqual(data['inner_unit_name'], 'Kg')
        self.assertEqual(data['cost_per_unit'], '1.120000')
        self.assertIsNone(data['moq'])

    def test_cost_per_unit_litre(self):
        row = self._create_mapping(
            product=self.oil,
            supplier_code='OIL-20L',
            supplier_product_name='Sunflower 20L',
            cost=Decimal('40'),
            outer_unit=self.drum,
            inner_qty=Decimal('20'),
            inner_unit=self.litre,
        )
        resp = self.client.get(f'/product/{self.oil.id}/suppliers/{row.id}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['inner_unit_name'], 'Litre')
        self.assertEqual(data['cost_per_unit'], '2.000000')

    def test_null_cost_is_null_per_unit(self):
        row = self._create_mapping(cost=None)
        resp = self.client.get(f'/product/{self.starch.id}/suppliers/{row.id}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(resp.json()['data']['cost_per_unit'])

    def test_moq_round_trip(self):
        resp = self.client.post(
            f'/product/{self.starch.id}/suppliers/',
            data=json.dumps({
                'supplier_id': self.supplier.id,
                'supplier_code': 'STARCH-MOQ',
                'supplier_product_name': 'Starch 25kg',
                'cost': '28',
                'moq': 10,
                'outer_qty': '1',
                'outer_unit_id': self.case.id,
                'inner_qty': '25',
                'inner_unit_id': self.kg.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['moq'], 10)
        self.assertEqual(data['cost_per_unit'], '1.120000')

        patch = self.client.patch(
            f'/product/{self.starch.id}/suppliers/{data["id"]}/',
            data=json.dumps({'moq': None}),
            content_type='application/json',
        )
        self.assertEqual(patch.status_code, 200, patch.content)
        self.assertIsNone(patch.json()['data']['moq'])

    def test_moq_rejects_zero(self):
        resp = self.client.post(
            f'/product/{self.starch.id}/suppliers/',
            data=json.dumps({
                'supplier_id': self.supplier.id,
                'supplier_code': 'STARCH-BAD-MOQ',
                'supplier_product_name': 'Starch 25kg',
                'moq': 0,
                'outer_qty': '1',
                'outer_unit_id': self.case.id,
                'inner_qty': '25',
                'inner_unit_id': self.kg.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('moq', resp.json()['message'])
