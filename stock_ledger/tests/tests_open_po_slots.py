"""Open PO slots on Global Stocks balances (remaining qty, date, number)."""

from datetime import date
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
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from stock_ledger.models import StockLot, StockLotOrigin
from stock_ledger.util import services


class OpenPoSlotsOnBalancesTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=401, name='Open PO Class')
        Category.objects.create(id=401, name='Open PO Cat')
        Range.objects.create(id=401, name='Open PO Range')
        self.unit = Unit.objects.create(id=401, name='kg')
        self.case = Unit.objects.create(id=402, name='CASE')
        self.wh = Location.objects.create(id=401, name='Open PO WH', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.wh, role=LocationRole.STORAGE,
        )
        self.supplier = Location.objects.create(
            id=402, name='Open PO Sup', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name='Peas',
            recipe_code=f'PEA-{uuid4().hex[:4]}',
            product_class_id=401,
            category_id=401,
            range_id=401,
            unit=self.unit,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        lot = StockLot.objects.create(
            product=self.product,
            trace_number='T-OPEN-PO',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        services.receipt(
            idempotency_key=f'open-po-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )

    def _po(
        self,
        *,
        number,
        status,
        expected,
        qty_ordered,
        qty_received,
        qty_balance,
        product_supplier=None,
    ):
        po = PurchaseOrder.objects.create(
            number=number,
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=status,
            ordered_at=date(2026, 9, 1),
            expected_at=expected,
            external_number=number,
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po,
            line_no=1,
            product=self.product,
            product_supplier=product_supplier,
            unit=self.unit,
            qty_ordered=qty_ordered,
            qty_received=qty_received,
            qty_balance=qty_balance,
        )
        return po

    def test_balances_list_open_pos_by_delivery_date_remaining_qty(self):
        po_partial = self._po(
            number='43123',
            status=PurchaseOrderStatus.PARTIAL,
            expected=date(2026, 9, 20),
            qty_ordered=Decimal('90'),
            qty_received=Decimal('40'),
            qty_balance=Decimal('50'),
        )
        self._po(
            number='44001',
            status=PurchaseOrderStatus.ORDERED,
            expected=date(2026, 9, 28),
            qty_ordered=Decimal('50'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('50'),
        )
        self._po(
            number='DRAFT1',
            status=PurchaseOrderStatus.DRAFT,
            expected=date(2026, 9, 10),
            qty_ordered=Decimal('10'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('10'),
        )
        self._po(
            number='DONE1',
            status=PurchaseOrderStatus.RECEIVED,
            expected=date(2026, 9, 5),
            qty_ordered=Decimal('10'),
            qty_received=Decimal('10'),
            qty_balance=Decimal('0'),
        )
        resp = self.client.get(
            f'/stock/balances/?location_id={self.wh.id}'
            f'&product_id={self.product.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        slots = resp.json()['data'][0]['open_pos']
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]['po_number'], '43123')
        self.assertEqual(slots[0]['po_id'], po_partial.id)
        self.assertEqual(slots[0]['unit_name'], 'kg')
        self.assertEqual(Decimal(slots[0]['qty_remaining']), Decimal('50'))
        self.assertEqual(Decimal(slots[0]['qty_remaining_stock']), Decimal('50'))
        self.assertEqual(slots[0]['delivery_date'], '2026-09-20')
        self.assertEqual(slots[1]['po_number'], '44001')
        self.assertEqual(Decimal(slots[1]['qty_remaining']), Decimal('50'))
        self.assertEqual(slots[1]['delivery_date'], '2026-09-28')

    def test_open_po_unit_is_pack_not_inner_kg(self):
        mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='PEA-1X10',
            supplier_product_name='Peas 1x10kg',
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('10'),
            inner_unit=self.unit,
            is_default=True,
            is_active=True,
        )
        self._po(
            number='12744',
            status=PurchaseOrderStatus.ORDERED,
            expected=date(2026, 9, 24),
            qty_ordered=Decimal('10'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('10'),
            product_supplier=mapping,
        )
        resp = self.client.get(
            f'/stock/balances/?location_id={self.wh.id}'
            f'&product_id={self.product.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        slots = resp.json()['data'][0]['open_pos']
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]['unit_name'], 'CASE')
        self.assertEqual(Decimal(slots[0]['qty_remaining']), Decimal('10'))
        self.assertEqual(Decimal(slots[0]['qty_remaining_stock']), Decimal('100'))
        self.assertEqual(slots[0]['ship_to_location_id'], self.wh.id)

    def test_open_po_only_on_ship_to_location(self):
        other = Location.objects.create(id=403, name='Low Risk', visible=True)
        LocationRoleAssignment.objects.create(
            location=other, role=LocationRole.STORAGE,
        )
        lot = StockLot.objects.create(
            product=self.product,
            trace_number='T-OPEN-PO-2',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        services.receipt(
            idempotency_key=f'open-po-other-{uuid4()}',
            lot=lot,
            location_id=other.id,
            quantity=Decimal('8'),
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='PEA-1X10-B',
            supplier_product_name='Peas 1x10kg',
            outer_qty=Decimal('1'),
            outer_unit=self.case,
            inner_qty=Decimal('10'),
            inner_unit=self.unit,
            is_default=True,
            is_active=True,
        )
        self._po(
            number='12744',
            status=PurchaseOrderStatus.ORDERED,
            expected=date(2026, 9, 24),
            qty_ordered=Decimal('10'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('10'),
            product_supplier=mapping,
        )
        resp = self.client.get(f'/stock/balances/?product_id={self.product.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        by_loc = {row['location_id']: row for row in resp.json()['data']}
        self.assertEqual(len(by_loc[self.wh.id]['open_pos']), 1)
        self.assertEqual(by_loc[other.id]['open_pos'], [])
