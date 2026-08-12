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
from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchasing.services.receive import ReceiveError, receive_purchase_order
from stock_ledger.models import (
    StockEntry,
    StockPeriod,
    StockPeriodStatus,
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
        StockPeriod.objects.get_or_create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            defaults={'status': StockPeriodStatus.OPEN},
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

    def test_idempotent_replay_does_not_double_qty(self):
        key = f'po-idem-{uuid4()}'
        body = {
            'location_id': self.wh.id,
            'lines': [{
                'line_id': self.line.id,
                'quantity': '1',
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
        StockPeriod.objects.get_or_create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            defaults={'status': StockPeriodStatus.OPEN},
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
