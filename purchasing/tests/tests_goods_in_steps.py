"""Form GET steps bools + answers after queued receive."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductLabelMode,
    ProductSupplier,
    Range,
    Unit,
)
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchasing.services.delivery import create_delivery
from purchasing.services.goods_in_form import resolve_goods_in_form
from purchasing.services.header_qc import submit_header_qc
from purchasing.services.line_qc import submit_line_qc
from purchasing.services.receive import receive_purchase_order
from stock_ledger.util.entry_labels import mark_printed


class GoodsInStepsTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=101, name='Step Class')
        Category.objects.create(id=101, name='Step Cat')
        Range.objects.create(id=101, name='Step Range')
        self.kg = Unit.objects.create(id=101, name='Kg')
        self.bag = Unit.objects.create(id=102, name='Bag')
        self.wh = Location.objects.create(id=101, name='Step WH', visible=True)
        self.supplier = Location.objects.create(
            id=102, name='Step Supplier', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Step Salt {uuid4().hex[:8]}',
            recipe_code=f'ST{uuid4().hex[:6]}',
            product_class_id=101,
            category_id=101,
            range_id=101,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='SALT-STEP',
            supplier_product_name='Salt',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'STP-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
            checked_at=timezone.now(),
            checked_by_user_id=1,
        )
        self.line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_no=1,
            product=self.product,
            product_supplier=mapping,
            unit=self.kg,
            qty_ordered=Decimal('2'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('2'),
            multiplier=mapping.multiplier,
            shape_format_label=mapping.shape_format_label,
            unit_cost=Decimal('1'),
            line_check_ok=True,
            use_by=date.today() + timedelta(days=30),
            trace_number=f'T{uuid4().hex[:6]}',
        )

    def test_queued_receive_steps_flip_after_print_and_post(self):
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': f'step-q-{uuid4()}',
                    'label_format': 'pallet',
                    'label_count': 1,
                }],
            },
        )
        delivery_id = data['delivery_id']
        entry_id = data['receive_results'][0]['stock_entry_id']
        form = resolve_goods_in_form(self.po.id, delivery_id=delivery_id)
        line_steps = form['steps']['lines'][0]
        self.assertTrue(form['steps']['header_qc'])
        self.assertTrue(line_steps['line_qc'])
        self.assertTrue(line_steps['received'])
        self.assertFalse(line_steps['print_label'])
        self.assertFalse(line_steps['verify_label'])
        self.assertFalse(line_steps['posted'])
        self.assertEqual(form['steps']['current'], 'print_label')
        self.assertEqual(form['resume_delivery_id'], delivery_id)
        self.assertEqual(line_steps['labels'][0]['entry_id'], entry_id)
        answers = form['answers']['lines'][str(self.line.id)]
        self.assertEqual(answers['qty_queued'], '2')

        listed = Client().get(f'/purchasing/pos/?supplier_id={self.supplier.id}')
        self.assertEqual(listed.status_code, 200, listed.content)
        row = next(
            item for item in listed.json()['data']['results'] if item['id'] == self.po.id
        )
        self.assertTrue(row['steps']['header_qc'])
        self.assertFalse(row['steps']['print_label'])
        self.assertFalse(row['steps']['verify_label'])
        self.assertEqual(row['steps']['queued_label_count'], 1)

        mark_printed(entry_id=entry_id)
        printed = resolve_goods_in_form(self.po.id, delivery_id=delivery_id)
        printed_line = printed['steps']['lines'][0]
        self.assertTrue(printed_line['print_label'])
        self.assertFalse(printed_line['verify_label'])
        self.assertEqual(printed['steps']['current'], 'verify_label')

        verify = Client().post(
            f'/stock/entries/{entry_id}/labels/verify/',
            data=f'{{"code":"E{entry_id}","post_stock":true}}',
            content_type='application/json',
        )
        self.assertEqual(verify.status_code, 200, verify.content)
        done = resolve_goods_in_form(self.po.id, delivery_id=delivery_id)
        done_line = done['steps']['lines'][0]
        self.assertTrue(done_line['print_label'])
        self.assertTrue(done_line['verify_label'])
        self.assertTrue(done_line['posted'])
        self.assertEqual(done['steps']['current'], 'done')
        self.assertEqual(done['answers']['lines'][str(self.line.id)]['qty_queued'], '0')

    def test_header_and_line_check_flags_are_per_question(self):
        delivery = create_delivery(self.po.id)
        empty = resolve_goods_in_form(self.po.id, delivery_id=delivery.id)
        self.assertIn('damaged_product', empty['steps']['header'])
        self.assertFalse(empty['steps']['header']['damaged_product'])
        self.assertIn('spec_check', empty['steps']['lines'][0]['checks'])
        self.assertFalse(empty['steps']['lines'][0]['checks']['spec_check'])

        submit_header_qc(
            self.po.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 1,
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {'value': False},
                },
            },
        )
        submit_line_qc(
            self.po.id,
            self.line.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 1,
                'answers': {'spec_check': {'value': True}},
            },
        )
        filled = resolve_goods_in_form(self.po.id, delivery_id=delivery.id)
        self.assertTrue(filled['steps']['header']['damaged_product'])
        self.assertTrue(filled['steps']['header']['reject_delivery'])
        self.assertFalse(
            filled['answers']['header']['damaged_product']['value'],
        )
        self.assertTrue(filled['steps']['lines'][0]['checks']['spec_check'])
        self.assertTrue(
            filled['answers']['lines'][str(self.line.id)]['spec_check']['value'],
        )
