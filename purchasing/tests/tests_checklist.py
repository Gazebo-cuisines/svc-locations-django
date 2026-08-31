"""GET /checklist/ maps header items to steps + summary."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    ProductLabelMode,
    ProductSupplier,
    Range,
    Unit,
)
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchasing.services.adhoc_goods_in import start_adhoc_goods_in
from purchasing.services.checklist import build_checklist
from purchasing.services.delivery import create_delivery
from purchasing.services.draft_qc import draft_adhoc_header_qc, draft_header_qc


class ChecklistTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=51, name='Cl Class')
        Category.objects.create(id=51, name='Cl Cat')
        Range.objects.create(id=51, name='Cl Range')
        self.kg = Unit.objects.create(id=51, name='Kg')
        self.bag = Unit.objects.create(id=52, name='Bag')
        self.wh = Location.objects.create(id=51, name='Cl WH', visible=True)
        self.supplier = Location.objects.create(id=52, name='Cl Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.other = Product.objects.create(
            name=f'Clip {uuid4().hex[:8]}',
            recipe_code=f'CL{uuid4().hex[:6]}',
            product_class_id=51,
            category_id=51,
            range_id=51,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        mapping = ProductSupplier.objects.create(
            product=self.other,
            supplier=self.supplier,
            supplier_code='CLIP-CL',
            supplier_product_name='Clip',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('1'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'CL-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
        )
        PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_no=1,
            product=self.other,
            product_supplier=mapping,
            unit=self.kg,
            qty_ordered=Decimal('1'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('1'),
            multiplier=mapping.multiplier,
            shape_format_label=mapping.shape_format_label,
            unit_cost=Decimal('1'),
        )

    def test_po_pending_then_fail_needs_comment(self):
        delivery = create_delivery(self.po.id)
        path = (
            f'/purchasing/pos/{self.po.id}/deliveries/{delivery.id}/checklist/'
        )
        empty = self.client.get(path)
        self.assertEqual(empty.status_code, 200)
        data = empty.json()['data']
        self.assertEqual(data['checklist_version'], 1)
        by_code = {step['code']: step for step in data['steps']}
        self.assertEqual(by_code['delivery_date']['state'], 'done')
        self.assertEqual(by_code['delivery_date']['kind'], 'date')
        self.assertIsNone(by_code['delivery_date']['image_key'])
        self.assertEqual(by_code['damaged_product']['state'], 'pending')
        self.assertEqual(by_code['damaged_product']['kind'], 'bool')
        self.assertEqual(
            by_code['damaged_product']['image_key'],
            'damaged-product',
        )
        self.assertIn('damaged_product', data['summary']['blocking'])
        self.assertIn('reject_delivery', data['summary']['blocking'])

        draft_header_qc(
            self.po.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 3,
                'answers': {
                    'reject_delivery': {
                        'value': True,
                        'comment': 'wet pallets',
                    },
                },
            },
        )
        done = self.client.get(path).json()['data']
        reject = {s['code']: s for s in done['steps']}['reject_delivery']
        self.assertEqual(reject['state'], 'fail')
        self.assertEqual(reject['comment'], 'wet pallets')
        self.assertNotIn('reject_delivery', done['summary']['blocking'])

    def test_needs_comment_is_blocking(self):
        data = build_checklist({
            'suggested_delivery_date': '2026-08-30',
            'header': {
                'version': 1,
                'items': [{
                    'code': 'reject_delivery',
                    'label': 'Reject Delivery?',
                    'input_type': 'bool',
                    'required': True,
                    'is_critical': True,
                    'fail_when': 'true',
                    'allows_comment': True,
                    'sort_order': 10,
                }],
            },
            'saved_header_answers': {
                'reject_delivery': {'value': True, 'comment': None},
            },
        })
        step = {s['code']: s for s in data['steps']}['reject_delivery']
        self.assertEqual(step['state'], 'needs_comment')
        self.assertEqual(data['summary']['blocking'], ['reject_delivery'])

    def test_adhoc_and_404(self):
        form = start_adhoc_goods_in(
            product_id=self.other.id,
            location_id=self.wh.id,
        )
        session_id = form['session_id']
        draft_adhoc_header_qc(
            session_id,
            body={
                'checked_by_user_id': 3,
                'answers': {'damaged_product': {'value': False}},
            },
        )
        resp = self.client.get(
            f'/purchasing/adhoc-goods-in/{session_id}/checklist/',
        )
        self.assertEqual(resp.status_code, 200)
        by_code = {s['code']: s for s in resp.json()['data']['steps']}
        self.assertEqual(by_code['damaged_product']['state'], 'done')
        self.assertFalse(by_code['damaged_product']['value'])
        self.assertNotIn('damaged_product', resp.json()['data']['summary']['blocking'])

        missing = self.client.get(
            f'/purchasing/pos/{self.po.id}/deliveries/999999/checklist/',
        )
        self.assertEqual(missing.status_code, 404)
