"""Category direct_consume: inherit flag + PO receive posts receipt+issue."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.goods_in import (
    category_has_direct_consume,
    product_is_direct_consume,
)
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
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.goods_in_form import resolve_goods_in_form
from purchasing.services.receive import ReceiveError, receive_purchase_order
from stock_ledger.models import StockBalance, StockEntry, StockEntryType


class DirectConsumeHelperTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=401, name='DC Class')
        Range.objects.create(id=401, name='DC Range')
        self.unit = Unit.objects.create(id=401, name='grams')
        self.root = Category.objects.create(id=401, name='Raw Materials')
        self.fresh = Category.objects.create(
            id=402, name='VEG - FRESH', parent=self.root, direct_consume=True,
        )
        self.child = Category.objects.create(
            id=403, name='Leafy', parent=self.fresh,
        )
        self.other = Category.objects.create(id=404, name='SPICES', parent=self.root)
        self.wh = Location.objects.create(id=401, name='DC WH', visible=True)

    def test_flag_inherits_to_descendant(self):
        self.assertTrue(category_has_direct_consume(self.fresh))
        self.assertTrue(category_has_direct_consume(self.child))
        self.assertFalse(category_has_direct_consume(self.root))
        self.assertFalse(category_has_direct_consume(self.other))

    def test_product_helper(self):
        product = Product.objects.create(
            name='Coriander',
            recipe_code=f'DC{uuid4().hex[:6]}',
            product_class_id=401,
            category=self.child,
            range_id=401,
            unit=self.unit,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.assertTrue(product_is_direct_consume(product))
        product.category = self.other
        product.save(update_fields=['category'])
        product.refresh_from_db()
        self.assertFalse(product_is_direct_consume(product))


class DirectConsumeReceiveTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=411, name='DC Rec Class')
        Range.objects.create(id=411, name='DC Rec Range')
        self.grams = Unit.objects.create(id=411, name='grams')
        self.bag = Unit.objects.create(id=412, name='Bag')
        self.root = Category.objects.create(id=411, name='Raw Materials')
        self.fresh = Category.objects.create(
            id=412, name='VEG - FRESH', parent=self.root, direct_consume=True,
        )
        self.wh = Location.objects.create(id=411, name='DC Rec WH', visible=True)
        self.supplier = Location.objects.create(
            id=412, name='DC Rec Supplier', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Fresh Herb {uuid4().hex[:6]}',
            recipe_code=f'FH{uuid4().hex[:6]}',
            product_class_id=411,
            category=self.fresh,
            range_id=411,
            unit=self.grams,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='HERB-1',
            supplier_product_name='Herb bag',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('500'),
            inner_unit=self.grams,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'DCT-{uuid4().hex[:6]}',
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
            unit=self.grams,
            qty_ordered=Decimal('2'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('2'),
            multiplier=self.mapping.multiplier,
            shape_format_label=self.mapping.shape_format_label,
            unit_cost=Decimal('1'),
            line_check_ok=True,
            use_by=date.today() + timedelta(days=2),
            trace_number=f'T{uuid4().hex[:6]}',
        )

    def test_form_exposes_direct_consume(self):
        form = resolve_goods_in_form(self.po.id)
        self.assertTrue(form['lines'][0]['direct_consume'])

    def test_receive_posts_receipt_and_issue_balance_zero(self):
        key = f'dc-recv-{uuid4()}'
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
        self.assertTrue(row['direct_consume'])
        self.assertIsNotNone(row.get('issue_entry_id'))
        self.assertEqual(row['qty_balance'], '0')
        self.assertIsNone(row.get('goods_in_label'))

        receipt = StockEntry.objects.get(pk=row['stock_entry_id'])
        issue = StockEntry.objects.get(pk=row['issue_entry_id'])
        self.assertEqual(receipt.entry_type, StockEntryType.RECEIPT)
        self.assertEqual(issue.entry_type, StockEntryType.ISSUE)
        self.assertEqual(issue.source_entry_id, receipt.id)
        self.assertEqual(abs(issue.quantity), receipt.quantity)
        self.assertIn('direct_consume', issue.remarks or '')

        bal = StockBalance.objects.filter(
            lot_id=row['lot_id'], location_id=self.wh.id,
        ).first()
        self.assertIsNone(bal)

        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('2'))
        self.assertTrue(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

    def test_receive_rejects_label_and_queue(self):
        with self.assertRaises(ReceiveError) as ctx:
            receive_purchase_order(
                self.po.id,
                body={
                    'location_id': self.wh.id,
                    'lines': [{
                        'line_id': self.line.id,
                        'quantity': '1',
                        'idempotency_key': f'dc-label-{uuid4()}',
                        'label_format': 'pallet',
                        'label_count': 1,
                    }],
                },
            )
        self.assertIn('cannot use labels', str(ctx.exception))

        with self.assertRaises(ReceiveError) as ctx:
            receive_purchase_order(
                self.po.id,
                body={
                    'location_id': self.wh.id,
                    'lines': [{
                        'line_id': self.line.id,
                        'quantity': '1',
                        'idempotency_key': f'dc-queue-{uuid4()}',
                        'queue_stock': True,
                    }],
                },
            )
        self.assertIn('cannot queue stock', str(ctx.exception))

    def test_normal_category_unchanged(self):
        spice = Category.objects.create(
            id=413, name='SPICES', parent=self.root, direct_consume=False,
        )
        product = Product.objects.create(
            name=f'Spice {uuid4().hex[:6]}',
            recipe_code=f'SP{uuid4().hex[:6]}',
            product_class_id=411,
            category=spice,
            range_id=411,
            unit=self.grams,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        mapping = ProductSupplier.objects.create(
            product=product,
            supplier=self.supplier,
            supplier_code='SPICE-1',
            supplier_product_name='Spice bag',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('1000'),
            inner_unit=self.grams,
            is_default=True,
            is_active=True,
        )
        po = PurchaseOrder.objects.create(
            number=f'NORM-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
            checked_at=timezone.now(),
            checked_by_user_id=1,
        )
        line = PurchaseOrderLine.objects.create(
            purchase_order=po,
            line_no=1,
            product=product,
            product_supplier=mapping,
            unit=self.grams,
            qty_ordered=Decimal('1'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('1'),
            multiplier=mapping.multiplier,
            shape_format_label=mapping.shape_format_label,
            unit_cost=Decimal('1'),
            line_check_ok=True,
            use_by=date.today() + timedelta(days=30),
            trace_number=f'T{uuid4().hex[:6]}',
        )
        data = receive_purchase_order(
            po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': line.id,
                    'quantity': '1',
                    'idempotency_key': f'norm-{uuid4()}',
                }],
            },
        )
        row = data['receive_results'][0]
        self.assertFalse(row['direct_consume'])
        self.assertIsNone(row.get('issue_entry_id'))
        bal = StockBalance.objects.get(lot_id=row['lot_id'], location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('1000'))
        self.assertEqual(
            StockEntry.objects.filter(
                lot_id=row['lot_id'], entry_type=StockEntryType.ISSUE,
            ).count(),
            0,
        )
