import json
from decimal import Decimal

from django.test import TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductSupplier,
    Unit,
)


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
        self.assertEqual(data['cost'], '28')
        self.assertEqual(data['multiplier'], '25')
        self.assertEqual(data['inner_unit_name'], 'Kg')
        self.assertEqual(data['cost_unit_name'], 'Kg')
        self.assertEqual(data['cost_per_unit'], '1.12')
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
        self.assertEqual(data['cost_unit_name'], 'Litre')
        self.assertEqual(data['cost_per_unit'], '2')

    def test_cost_unit_follows_inner_not_product_base(self):
        grams = Unit.objects.create(id=5, name='grams')
        product = Product.objects.create(
            name='SOYA MINCE',
            product_class_id=1,
            category_id=1,
            unit=grams,
            source_container=self.wh,
            destination_container=self.wh,
        )
        row = self._create_mapping(
            product=product,
            supplier_code='174346',
            supplier_product_name='NATURAL SOYA MINCE',
            cost=Decimal('26.85'),
            inner_qty=Decimal('15'),
            inner_unit=self.kg,
        )
        resp = self.client.get(f'/product/{product.id}/suppliers/{row.id}/')
        data = resp.json()['data']
        self.assertEqual(data['base_unit_name'], 'grams')
        self.assertEqual(data['cost_unit_name'], 'Kg')
        self.assertEqual(data['cost_per_unit'], '1.79')

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
        self.assertEqual(data['cost_per_unit'], '1.12')

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


class PurchaseCostingReportTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Raw')
        ProductClass.objects.create(id=2, name='Finished')
        self.rm_root = Category.objects.create(id=73, name='Raw Materials')
        self.rm_child = Category.objects.create(
            id=9, name='GROUND', parent=self.rm_root,
        )
        self.pack_root = Category.objects.create(id=79, name='Packaging Material')
        self.other_cat = Category.objects.create(id=99, name='Finished Goods')
        self.kg = Unit.objects.create(id=1, name='Kg')
        self.case = Unit.objects.create(id=3, name='Case')
        self.wh = Location.objects.create(id=1, name='WH', visible=True)
        self.supplier_a = Location.objects.create(id=2, name='Salt Co A', visible=True)
        self.supplier_b = Location.objects.create(id=3, name='Salt Co B', visible=True)
        for loc in (self.supplier_a, self.supplier_b):
            LocationRoleAssignment.objects.create(
                location=loc, role=LocationRole.SUPPLIER,
            )
        self.salt = Product.objects.create(
            name='SALT',
            recipe_code='RM-SALT',
            alternate_recipe_code='ALT-SALT',
            product_class_id=1,
            category=self.rm_child,
            unit=self.kg,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.film = Product.objects.create(
            name='SLEEVE FILM',
            recipe_code='PK-FILM',
            product_class_id=1,
            category=self.pack_root,
            unit=self.kg,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.fg = Product.objects.create(
            name='FINISHED PIE',
            product_class_id=2,
            category=self.other_cat,
            unit=self.kg,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.salt_a = ProductSupplier.objects.create(
            product=self.salt,
            supplier=self.supplier_a,
            supplier_code='SALT-A',
            supplier_product_name='Salt 25kg A',
            cost=Decimal('10'),
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('25'),
            inner_unit=self.kg,
            is_default=True,
        )
        self.salt_b = ProductSupplier.objects.create(
            product=self.salt,
            supplier=self.supplier_b,
            supplier_code='SALT-B',
            supplier_product_name='Salt 25kg B',
            cost=Decimal('12'),
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('25'),
            inner_unit=self.kg,
        )
        self.film_row = ProductSupplier.objects.create(
            product=self.film,
            supplier=self.supplier_a,
            supplier_code='FILM-1',
            supplier_product_name='Film roll',
            cost=None,
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
        )
        ProductSupplier.objects.create(
            product=self.fg,
            supplier=self.supplier_a,
            supplier_code='FG-1',
            supplier_product_name='Should not appear',
            cost=Decimal('99'),
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('1'),
            inner_unit=self.kg,
        )

    def test_includes_rm_and_pack_excludes_finished(self):
        resp = self.client.get('/product/purchase-costing-report/')
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['data']
        product_ids = {row['product_id'] for row in rows}
        self.assertEqual(product_ids, {self.salt.id, self.film.id})
        salt_rows = [row for row in rows if row['product_id'] == self.salt.id]
        self.assertEqual(len(salt_rows), 2)
        sample = salt_rows[0]
        self.assertEqual(sample['category_name'], 'GROUND')
        self.assertEqual(sample['recipe_code'], 'RM-SALT')
        self.assertEqual(sample['alternate_recipe_code'], 'ALT-SALT')
        self.assertEqual(sample['base_unit_id'], self.kg.id)
        self.assertEqual(sample['cost_unit_name'], 'Kg')

    def test_category_id_packaging_root(self):
        resp = self.client.get(
            '/product/purchase-costing-report/',
            {'category_id': self.pack_root.id},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['product_id'], self.film.id)

    def test_category_id_raw_includes_child(self):
        resp = self.client.get(
            '/product/purchase-costing-report/',
            {'category_id': self.rm_root.id},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['data']
        self.assertEqual({row['product_id'] for row in rows}, {self.salt.id})

    def test_has_cost_filter(self):
        resp = self.client.get(
            '/product/purchase-costing-report/',
            {'has_cost': 'true'},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['data']
        self.assertEqual({row['id'] for row in rows}, {self.salt_a.id, self.salt_b.id})

    def test_supplier_filter(self):
        resp = self.client.get(
            '/product/purchase-costing-report/',
            {'supplier_id': self.supplier_b.id},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], self.salt_b.id)
