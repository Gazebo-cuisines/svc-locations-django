"""PO receive parity + stock receipt PO gate."""

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
    PurchaseOrderHistory,
    PurchaseOrderHistoryEvent,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.receive import ReceiveError, receive_purchase_order
from stock_ledger.models import (
    StockEntry,
    StockUnit,
)


class PoReceiveParityTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=81, name='PO Class')
        Category.objects.create(id=81, name='PO Cat')
        Range.objects.create(id=81, name='PO Range')
        self.kg = Unit.objects.create(id=81, name='Kg')
        self.bag = Unit.objects.create(id=82, name='Bag')
        self.wh = Location.objects.create(id=81, name='PO WH', visible=True)
        self.supplier = Location.objects.create(id=82, name='PO Supplier', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'PO Salt {uuid4().hex[:8]}',
            recipe_code=f'PO{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
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
            number=f'POT-{uuid4().hex[:6]}',
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
            product_supplier=self.mapping,
            unit=self.kg,
            qty_ordered=Decimal('2'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('2'),
            multiplier=self.mapping.multiplier,
            shape_format_label=self.mapping.shape_format_label,
            unit_cost=Decimal('1'),
            line_check_ok=True,
            use_by=date.today() + timedelta(days=30),
            trace_number=f'T{uuid4().hex[:6]}',
        )

    def test_receive_applies_pack_multiplier_and_rich_entry(self):
        key = f'po-recv-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': key,
                }],
            },
            audit={'lan_username': 'tester', 'actor_user_id': 9},
        )
        row = data['receive_results'][0]
        self.assertEqual(row['quantity_ordered_units'], '2')
        self.assertEqual(row['quantity_stock'], '20')
        self.assertEqual(row['qty_balance'], '0')
        self.assertFalse(row['idempotent_replay'])
        entry = row['entry']
        self.assertEqual(entry['quantity'], '20')
        self.assertEqual(entry['pack_quantity'], '2')
        self.assertEqual(entry['shape_multiplier'], '10')
        self.assertTrue(entry.get('shape_format_label'))
        self.assertEqual(entry['lan_username'], 'tester')

        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('2'))
        self.assertTrue(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

    def test_receive_print_labels(self):
        key = f'po-print-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': key,
                    'print_unit_count': 2,
                    'print_quantity_per_unit': '10',
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertEqual(len(row['units']), 2)
        self.assertEqual(StockUnit.objects.filter(created_by_entry_id=row['stock_entry_id']).count(), 2)

    def test_receive_goods_in_entry_label(self):
        key = f'po-elabel-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': key,
                    'label_format': 'box',
                    'label_count': 2,
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertEqual(row['transaction_count'], 2)
        self.assertEqual(len(row['transactions']), 2)
        codes = {t['entry_code'] for t in row['transactions']}
        self.assertEqual(len(codes), 2)
        for tx in row['transactions']:
            self.assertEqual(tx['goods_in_label']['copies'], 1)
            self.assertEqual(tx['label']['label_format'], 'box')
            self.assertEqual(tx['posting_status'], 'queued')
        self.assertEqual(row['entry_code'], row['transactions'][0]['entry_code'])
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertFalse(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)
        self.assertEqual(Decimal(row['qty_queued']), Decimal('2'))

    def test_admin_line_label_drives_receive_split(self):
        self.line.label_format = 'box'
        self.line.label_count = 2
        self.line.qty_ordered = Decimal('2')
        self.line.qty_balance = Decimal('2')
        self.line.save(
            update_fields=[
                'label_format', 'label_count', 'qty_ordered', 'qty_balance', 'updated_at',
            ],
        )
        key = f'po-admin-label-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': key,
                    # warehouse must not override admin plan
                    'label_format': 'pallet',
                    'label_count': 1,
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertEqual(row['label_format'], 'box')
        self.assertEqual(row['transaction_count'], 2)

    def test_queued_receive_completes_po_only_after_post(self):
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': f'po-qpost-{uuid4()}',
                    'label_format': 'pallet',
                    'label_count': 1,
                }],
            },
        )
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)
        with self.assertRaises(ReceiveError):
            receive_purchase_order(
                self.po.id,
                body={
                    'location_id': self.wh.id,
                    'lines': [{
                        'line_id': self.line.id,
                        'quantity': '2',
                        'idempotency_key': f'po-qpost-dup-{uuid4()}',
                        'label_format': 'pallet',
                        'label_count': 1,
                    }],
                },
            )
        entry_id = data['receive_results'][0]['stock_entry_id']
        verify = Client().post(
            f'/stock/entries/{entry_id}/labels/verify/',
            data=(
                f'{{"code":"E{entry_id}","post_stock":true}}'
            ),
            content_type='application/json',
        )
        self.assertEqual(verify.status_code, 200, verify.content)
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('2'))
        self.assertTrue(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

    def test_cancel_queued_receive_allows_receive_again(self):
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': f'po-qcan-{uuid4()}',
                    'label_format': 'pallet',
                    'label_count': 1,
                }],
            },
        )
        entry_id = data['receive_results'][0]['stock_entry_id']
        cancel = Client().post(f'/stock/entries/{entry_id}/cancel/')
        self.assertEqual(cancel.status_code, 200, cancel.content)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        again = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '2',
                    'idempotency_key': f'po-qcan2-{uuid4()}',
                    'label_format': 'pallet',
                    'label_count': 1,
                }],
            },
        )
        self.assertEqual(
            Decimal(again['receive_results'][0]['qty_queued']),
            Decimal('2'),
        )

    def test_idempotent_replay_does_not_double_qty(self):
        key = f'po-idem-{uuid4()}'
        body = {
            'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '1',
                    'shortfall_reason': 'short_delivery',
                    'idempotency_key': key,
                }],
        }
        first = receive_purchase_order(self.po.id, body=body)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('1'))
        self.assertEqual(self.line.qty_balance, Decimal('1'))

        second = receive_purchase_order(self.po.id, body=body)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('1'))
        self.assertEqual(self.line.qty_balance, Decimal('1'))
        self.assertTrue(second['receive_results'][0]['idempotent_replay'])
        self.assertEqual(
            first['receive_results'][0]['stock_entry_id'],
            second['receive_results'][0]['stock_entry_id'],
        )
        self.assertEqual(
            StockEntry.objects.filter(idempotency_key=key).count(),
            1,
        )

    def test_short_receive_requires_reason(self):
        with self.assertRaises(ReceiveError) as ctx:
            receive_purchase_order(
                self.po.id,
                body={
                    'location_id': self.wh.id,
                    'lines': [{
                        'line_id': self.line.id,
                        'quantity': '1',
                        'idempotency_key': f'po-short-{uuid4()}',
                    }],
                },
            )
        self.assertIn('shortfall_reason', str(ctx.exception))

    def test_short_delivery_leaves_balance(self):
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '1',
                    'shortfall_reason': 'short_delivery',
                    'idempotency_key': f'po-await-{uuid4()}',
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertEqual(row['qty_received'], '1')
        self.assertEqual(row['qty_rejected'], '0')
        self.assertEqual(row['qty_balance'], '1')
        self.assertFalse(row['needs_credit_note'])
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertFalse(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.PARTIAL)
        note = PurchaseOrderHistory.objects.get(
            purchase_order=self.po,
            event_type=PurchaseOrderHistoryEvent.NOTE,
        )
        self.assertEqual(note.payload['shortfall_reason'], 'short_delivery')
        self.assertFalse(note.payload['needs_credit_note'])

    def test_reject_remainder_closes_for_credit(self):
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '1',
                    'shortfall_reason': 'damaged',
                    'remarks': '2 boxes crushed',
                    'idempotency_key': f'po-rej-{uuid4()}',
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertEqual(row['qty_received'], '1')
        self.assertEqual(row['qty_rejected'], '1')
        self.assertEqual(row['qty_balance'], '0')
        self.assertTrue(row['needs_credit_note'])
        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertTrue(self.line.line_closed)
        self.assertEqual(self.line.shortfall_reason, 'damaged')
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)
        event = PurchaseOrderHistory.objects.get(
            purchase_order=self.po,
            event_type=PurchaseOrderHistoryEvent.NON_CONFORMANCE,
        )
        self.assertTrue(event.payload['needs_credit_note'])
        reasons = {item['code'] for item in data['shortfall_reasons']}
        self.assertIn('short_delivery', reasons)
        self.assertIn('damaged', reasons)

    def test_over_receive_blocked(self):
        with self.assertRaises(ReceiveError):
            receive_purchase_order(
                self.po.id,
                body={
                    'location_id': self.wh.id,
                    'lines': [{
                        'line_id': self.line.id,
                        'quantity': '3',
                        'idempotency_key': f'po-over-{uuid4()}',
                    }],
                },
            )


class StockReceiptPoGateTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=83, name='Gate Class')
        Category.objects.create(id=83, name='Gate Cat')
        Range.objects.create(id=83, name='Gate Range')
        self.unit = Unit.objects.create(id=83, name='Kg')
        self.wh = Location.objects.create(id=83, name='Gate WH', visible=True)
        self.supplier = Location.objects.create(id=84, name='Gate Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Gate Prod {uuid4().hex[:8]}',
            recipe_code=f'G{uuid4().hex[:6]}',
            product_class_id=83,
            category_id=83,
            range_id=83,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.wh,
        )

    def test_stock_receipt_rejects_po_number(self):
        client = Client()
        resp = client.post(
            '/stock/receipt/',
            data={
                'idempotency_key': f'gate-{uuid4()}',
                'product_id': self.product.id,
                'location_id': self.wh.id,
                'quantity': '1',
                'supplier_id': self.supplier.id,
                'po_number': 'PO7',
                'origin': 'purchase',
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('purchasing/pos', (resp.json().get('message') or '').lower())
