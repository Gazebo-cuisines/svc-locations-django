"""PO qty rollback when a PO-linked receipt is removed via Stock Management."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
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
    PurchaseOrderDeliveryLine,
    PurchaseOrderHistory,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.delivery import create_delivery
from purchasing.services.header_qc import submit_header_qc
from purchasing.services.line_qc import submit_line_qc
from purchasing.services.po_qty import unapply_po_receipt_from_entry
from purchasing.services.receive import receive_purchase_order
from stock_ledger.models import StockEntry, StockEntryType
from stock_ledger.util import entry_labels, entry_posting, manage
from users_rbac.models import AdminAccess, AdminArea, Department, RbacUser, UserDepartment


class PoReceiptRollbackTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=84, name='RB Class')
        Category.objects.create(id=84, name='RB Cat')
        Range.objects.create(id=84, name='RB Range')
        self.kg = Unit.objects.create(id=84, name='Kg')
        self.bag = Unit.objects.create(id=85, name='Bag')
        self.wh = Location.objects.create(id=84, name='RB WH', visible=True)
        self.supplier = Location.objects.create(id=85, name='RB Supplier', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'RB Salt {uuid4().hex[:8]}',
            recipe_code=f'RB{uuid4().hex[:6]}',
            product_class_id=84,
            category_id=84,
            range_id=84,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='RB-1X10',
            supplier_product_name='RB 1x10kg',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'RB-{uuid4().hex[:6]}',
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
            qty_ordered=Decimal('25'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('25'),
            multiplier=self.mapping.multiplier,
            shape_format_label=self.mapping.shape_format_label,
            unit_cost=Decimal('1'),
            line_check_ok=True,
            use_by=date.today() + timedelta(days=30),
            trace_number=f'RB{uuid4().hex[:6]}',
        )
        self.manager = RbacUser.objects.create(
            cognito_sub=f'sub-rb-{uuid4().hex[:8]}',
            username='rbmgr',
        )
        UserDepartment.objects.create(user=self.manager, department=Department.ADMIN)
        AdminAccess.objects.create(
            user=self.manager,
            area=AdminArea.STOCK_MANAGEMENT,
        )

    def _receive_and_post(self, *, purchase_qty: str = '25'):
        delivery = create_delivery(self.po.id)
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
        key = f'rb-recv-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': purchase_qty,
                    'idempotency_key': key,
                    'label_format': 'pallet',
                    'label_count': 1,
                    'queue_stock': True,
                }],
            },
            delivery_id=delivery.id,
            audit={'actor_user_id': self.manager.id, 'lan_username': 'rbmgr'},
        )
        entry_id = data['receive_results'][0]['stock_entry_id']
        entry_labels.create_entry_label(
            entry=StockEntry.objects.get(pk=entry_id),
            label_format='pallet',
            label_count=1,
        )
        entry_labels.verify_label(entry_id=entry_id, code=f'E{entry_id}')
        entry_posting.post_entry(entry_id=entry_id)
        return entry_id, delivery.id

    def test_unapply_posted_receipt_reopens_po_line(self):
        entry_id, delivery_id = self._receive_and_post(purchase_qty='25')
        entry = StockEntry.objects.get(pk=entry_id)

        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('25'))
        self.assertEqual(self.line.qty_balance, Decimal('0'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.RECEIVED)

        result = unapply_po_receipt_from_entry(
            entry,
            reason='Posted 25 in error; actual 15',
            actor_user_id=self.manager.id,
            lan_username='rbmgr',
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['purchase_qty'], '25')

        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertEqual(self.line.qty_balance, Decimal('25'))
        self.assertFalse(self.line.line_closed)
        self.assertEqual(self.po.status, PurchaseOrderStatus.ORDERED)

        dline = PurchaseOrderDeliveryLine.objects.get(
            delivery_id=delivery_id, po_line_id=self.line.id,
        )
        self.assertEqual(dline.qty_received, Decimal('0'))
        self.assertTrue(
            PurchaseOrderHistory.objects.filter(
                purchase_order=self.po,
                remarks__icontains='rolled back',
            ).exists(),
        )

    def test_manage_remove_rolls_back_po(self):
        entry_id, _delivery_id = self._receive_and_post(purchase_qty='25')
        result = manage.remove_entry(
            entry_id=entry_id,
            reason='Wrong qty posted on PO receive',
            idempotency_key=f'rb-rm-{uuid4()}',
            actor_user_id=self.manager.id,
            lan_username='rbmgr',
        )
        self.assertIn(f'E{entry_id}', result['reversed_entry_codes'])
        self.assertEqual(len(result['po_rollbacks']), 1)
        self.assertEqual(result['po_rollbacks'][0]['purchase_qty'], '25')

        self.line.refresh_from_db()
        self.po.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertEqual(self.line.qty_balance, Decimal('25'))
        self.assertEqual(self.po.status, PurchaseOrderStatus.ORDERED)

    def test_unapply_immediate_receive(self):
        key = f'rb-immediate-{uuid4()}'
        data = receive_purchase_order(
            self.po.id,
            body={
                'location_id': self.wh.id,
                'lines': [{
                    'line_id': self.line.id,
                    'quantity': '25',
                    'idempotency_key': key,
                }],
            },
            audit={'actor_user_id': self.manager.id},
        )
        entry = StockEntry.objects.get(pk=data['receive_results'][0]['stock_entry_id'])
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('25'))

        result = unapply_po_receipt_from_entry(
            entry,
            reason='Immediate receive rollback test',
        )
        self.assertIsNotNone(result)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, Decimal('0'))
        self.assertEqual(self.line.qty_balance, Decimal('25'))
