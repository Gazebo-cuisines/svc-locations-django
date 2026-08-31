"""Sage PO number on create / list / update."""

import json
from uuid import uuid4

from django.test import Client, TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import Category, Product, ProductClass, Range, Unit
from purchasing.models import PurchaseOrderSource, PurchaseOrderStatus
from purchasing.services.po import create_purchase_order
class SagePoNumberTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=61, name='Sage Class')
        Category.objects.create(id=61, name='Sage Cat')
        Range.objects.create(id=61, name='Sage Range')
        self.unit = Unit.objects.create(id=61, name='Kg')
        self.wh = Location.objects.create(id=61, name='Sage WH', visible=True)
        self.supplier = Location.objects.create(id=62, name='Sage Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Sage Prod {uuid4().hex[:6]}',
            recipe_code=f'SG{uuid4().hex[:6]}',
            product_class_id=61,
            category_id=61,
            range_id=61,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.client = Client()
        self.sage_no = f'SAGE-{uuid4().hex[:8].upper()}'

    def _line(self):
        return {
            'product_id': self.product.id,
            'qty_ordered': '10',
        }

    def test_create_requires_sage_po_number(self):
        resp = self.client.post(
            '/purchasing/pos/',
            data=(
                '{'
                f'"supplier_id":{self.supplier.id},'
                f'"ship_to_location_id":{self.wh.id},'
                f'"lines":[{{"product_id":{self.product.id},"qty_ordered":"1"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('sage_po_number', resp.json()['message'])

    def test_create_and_list_by_sage_po_number(self):
        resp = self.client.post(
            '/purchasing/pos/',
            data=(
                '{'
                f'"supplier_id":{self.supplier.id},'
                f'"ship_to_location_id":{self.wh.id},'
                f'"sage_po_number":"{self.sage_no}",'
                f'"status":"ordered",'
                f'"lines":[{{"product_id":{self.product.id},"qty_ordered":"2"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['sage_po_number'], self.sage_no)
        self.assertEqual(data['source'], PurchaseOrderSource.SAGE)
        self.assertTrue(data['number'].startswith('PO'))
        self.assertEqual(data['id'], int(data['number'].replace('PO', '')))

        listed = self.client.get(
            f'/purchasing/pos/?sage_po_number={self.sage_no}',
        )
        self.assertEqual(listed.status_code, 200)
        rows = listed.json()['data']['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sage_po_number'], self.sage_no)

    def test_list_pipe_statuses(self):
        ordered = create_purchase_order(
            supplier_id=self.supplier.id,
            lines=[self._line()],
            sage_po_number=f'{self.sage_no}-O',
            status=PurchaseOrderStatus.ORDERED,
            require_sage_po_number=True,
        )
        create_purchase_order(
            supplier_id=self.supplier.id,
            lines=[self._line()],
            sage_po_number=f'{self.sage_no}-D',
            status=PurchaseOrderStatus.DRAFT,
            require_sage_po_number=True,
        )
        listed = self.client.get(
            '/purchasing/pos/?status=ordered|partial|received',
        )
        self.assertEqual(listed.status_code, 200)
        ids = {row['id'] for row in listed.json()['data']['results']}
        self.assertIn(ordered.id, ids)
        self.assertEqual(len(ids), 1)

    def test_create_with_line_label_plan(self):
        resp = self.client.post(
            '/purchasing/pos/',
            data=json.dumps({
                'supplier_id': self.supplier.id,
                'ship_to_location_id': self.wh.id,
                'sage_po_number': f'{self.sage_no}-BOX',
                'lines': [{
                    'product_id': self.product.id,
                    'qty_ordered': '5',
                    'label_format': 'box',
                    'label_count': 5,
                }],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        line = resp.json()['data']['lines'][0]
        self.assertEqual(line['label_format'], 'box')
        self.assertEqual(line['label_count'], 5)

    def test_duplicate_sage_po_number_rejected(self):
        create_purchase_order(
            supplier_id=self.supplier.id,
            lines=[self._line()],
            sage_po_number=self.sage_no,
            status=PurchaseOrderStatus.DRAFT,
            require_sage_po_number=True,
        )
        resp = self.client.post(
            '/purchasing/pos/',
            data=(
                '{'
                f'"supplier_id":{self.supplier.id},'
                f'"sage_po_number":"{self.sage_no}",'
                f'"lines":[{{"product_id":{self.product.id},"qty_ordered":"1"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
