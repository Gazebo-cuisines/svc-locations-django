"""Nested deliveries: reject, split receive, auto-close."""

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
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderDeliveryStatus,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.delivery import (
    DeliveryError,
    create_delivery,
    list_rejected_deliveries,
    unblock_rejected_delivery,
)
from purchasing.services.header_qc import submit_header_qc
from purchasing.services.line_qc import submit_line_qc
from purchasing.services.receive import receive_purchase_order


class NestedDeliveryTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=91, name='Del Class')
        Category.objects.create(id=91, name='Del Cat')
        Range.objects.create(id=91, name='Del Range')
        self.kg = Unit.objects.create(id=91, name='Kg')
        self.bag = Unit.objects.create(id=92, name='Bag')
        self.wh = Location.objects.create(id=91, name='Del WH', visible=True)
        self.supplier = Location.objects.create(id=92, name='Mitaka', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Salt {uuid4().hex[:8]}',
            recipe_code=f'SL{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='SALT-1X10',
            supplier_product_name='Salt 1x10kg',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'DEL-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
        )
        self.line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_no=1,
            product=self.product,
            product_supplier=self.mapping,
            unit=self.kg,
            qty_ordered=Decimal('5'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('5'),
            multiplier=self.mapping.multiplier,
            shape_format_label=self.mapping.shape_format_label,
            unit_cost=Decimal('1'),
        )

    def _header(self, delivery_id, *, reject=False):
        return submit_header_qc(
            self.po.id,
            delivery_id=delivery_id,
            body={
                'checked_by_user_id': 1,
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {
                        'value': reject,
                        'comment': 'wrong goods' if reject else None,
                    },
                },
            },
        )

    def _line_qc(self, delivery_id):
        return submit_line_qc(
            self.po.id,
            self.line.id,
            delivery_id=delivery_id,
            body={
                'checked_by_user_id': 1,
                'answers': {'spec_check': {'value': True}},
            },
        )

    def _receive(self, delivery_id, qty, *, shortfall=None):
        body = {
            'location_id': self.wh.id,
            'lines': [{
                'line_id': self.line.id,
                'quantity': str(qty),
                'idempotency_key': f'del-{uuid4()}',
            }],
        }
        if shortfall:
            body['lines'][0]['shortfall_reason'] = shortfall
        return receive_purchase_order(
            self.po.id, body=body, delivery_id=delivery_id,
        )

    def test_cannot_open_second_while_one_is_open(self):
        create_delivery(self.po.id)
        with self.assertRaises(DeliveryError) as ctx:
            create_delivery(self.po.id)
        self.assertIn('open delivery already exists', str(ctx.exception))

    def test_salt_reject_then_split_then_close(self):
        d1 = create_delivery(self.po.id)
        self._header(d1.id, reject=True)
        d1.refresh_from_db()
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(d1.status, PurchaseOrderDeliveryStatus.REJECTED)
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertEqual(self.line.qty_balance, Decimal('5'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.ORDERED)

        d2 = create_delivery(self.po.id)
        self._header(d2.id)
        self._line_qc(d2.id)
        self._receive(d2.id, 3, shortfall='short_delivery')
        d2.refresh_from_db()
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(d2.status, PurchaseOrderDeliveryStatus.RECEIVED)
        self.assertEqual(self.line.qty_received, Decimal('3'))
        self.assertEqual(self.line.qty_balance, Decimal('2'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)

        d3 = create_delivery(self.po.id)
        self._header(d3.id)
        self._line_qc(d3.id)
        self._receive(d3.id, 2)
        d3.refresh_from_db()
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(d3.status, PurchaseOrderDeliveryStatus.RECEIVED)
        self.assertEqual(self.line.qty_received, Decimal('5'))
        self.assertEqual(self.line.qty_balance, Decimal('0'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

        with self.assertRaises(DeliveryError) as ctx:
            create_delivery(self.po.id)
        self.assertIn('received', str(ctx.exception).lower())

        client = Client()
        timeline = client.get(f'/purchasing/pos/{self.po.id}/timeline/')
        self.assertEqual(timeline.status_code, 200)
        actions = {e['action'] for e in timeline.json()['data']}
        self.assertTrue({'create', 'reject', 'goods_in'} <= actions)

        resp = client.post(f'/purchasing/pos/{self.po.id}/deliveries/')
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_list_rejected_and_unblock(self):
        d1 = create_delivery(self.po.id)
        self._header(d1.id, reject=True)
        listed = list_rejected_deliveries()
        self.assertTrue(any(row['id'] == d1.id for row in listed))

        client = Client()
        resp = client.get('/purchasing/deliveries/rejected/')
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row['id'] for row in resp.json()['data']['results']}
        self.assertIn(d1.id, ids)

        unblocked = unblock_rejected_delivery(
            self.po.id,
            d1.id,
            reason='QC override — packaging OK',
            checked_by_user_id=99,
        )
        self.assertEqual(unblocked['status'], PurchaseOrderDeliveryStatus.OPEN)
        self.assertFalse(unblocked['reject_delivery'])
        d1.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(d1.status, PurchaseOrderDeliveryStatus.OPEN)
        self.assertFalse(d1.reject_delivery)
        self.assertFalse(self.po.reject_delivery)

        self._line_qc(d1.id)
        self._receive(d1.id, 5)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

        bad = client.post(
            f'/purchasing/pos/{self.po.id}/deliveries/{d1.id}/qc/header/unblock/',
            data='{"reason":"too late"}',
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400, bad.content)

    def test_alias_receive_auto_creates_first_delivery(self):
        self.po.checked_at = timezone.now()
        self.po.save(update_fields=['checked_at'])
        self.line.line_check_ok = True
        self.line.use_by = date.today() + timedelta(days=30)
        self.line.trace_number = f'T{uuid4().hex[:6]}'
        self.line.save()
        receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '5',
                    'idempotency_key': f'alias-{uuid4()}',
                }],
            },
        )
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)
        self.assertEqual(
            PurchaseOrderDelivery.objects.filter(purchase_order=self.po).count(),
            1,
        )

    def test_multi_line_same_delivery_stays_open(self):
        product_b = Product.objects.create(
            name=f'Salt B {uuid4().hex[:8]}',
            recipe_code=f'SB{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        mapping_b = ProductSupplier.objects.create(
            product=product_b,
            supplier=self.supplier,
            supplier_code='SALT-B-1X10',
            supplier_product_name='Salt B 1x10kg',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        line_b = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_no=2,
            product=product_b,
            product_supplier=mapping_b,
            unit=self.kg,
            qty_ordered=Decimal('4'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('4'),
            multiplier=mapping_b.multiplier,
            shape_format_label=mapping_b.shape_format_label,
            unit_cost=Decimal('1'),
        )

        delivery = create_delivery(self.po.id)
        self._header(delivery.id)
        self._line_qc(delivery.id)
        submit_line_qc(
            self.po.id,
            line_b.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 1,
                'answers': {'spec_check': {'value': True}},
            },
        )

        self._receive(delivery.id, 5)
        delivery.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(delivery.status, PurchaseOrderDeliveryStatus.OPEN)
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)

        receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': line_b.id,
                    'quantity': '4',
                    'idempotency_key': f'del-b-{uuid4()}',
                }],
            },
            delivery_id=delivery.id,
        )
        delivery.refresh_from_db()
        self.po.refresh_from_db()
        line_b.refresh_from_db()
        self.assertEqual(delivery.status, PurchaseOrderDeliveryStatus.RECEIVED)
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)
        self.assertEqual(line_b.qty_received, Decimal('4'))
        self.assertEqual(
            PurchaseOrderDelivery.objects.filter(purchase_order=self.po).count(),
            1,
        )

    def test_finish_delivery_closes_with_balance(self):
        delivery = create_delivery(self.po.id)
        self._header(delivery.id)
        self._line_qc(delivery.id)
        receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'finish_delivery': True,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'shortfall_reason': 'split_pallet',
                    'remarks': 'rest on next truck',
                    'idempotency_key': f'del-fin-{uuid4()}',
                }],
            },
            delivery_id=delivery.id,
        )
        delivery.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(delivery.status, PurchaseOrderDeliveryStatus.RECEIVED)
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)
        self.assertEqual(
            create_delivery(self.po.id).status,
            PurchaseOrderDeliveryStatus.OPEN,
        )
