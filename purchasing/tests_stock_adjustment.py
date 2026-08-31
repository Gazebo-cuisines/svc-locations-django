"""Stock Adjustment (Goods In tab C) — no QC."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from locations.models import Location
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
from purchasing.services.stock_adjustment import (
    StockAdjustmentError,
    receive_stock_adjustment,
)
from stock_ledger.models import StockEntry, StockPeriod, StockPeriodStatus


class StockAdjustmentTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=83, name='Adj Class')
        Category.objects.create(id=83, name='Adj Cat')
        Range.objects.create(id=83, name='Adj Range')
        self.kg = Unit.objects.create(id=83, name='Kg')
        self.bag = Unit.objects.create(id=84, name='Bag')
        self.wh = Location.objects.create(id=83, name='Adj WH', visible=True)
        self.supplier = Location.objects.create(
            id=84, name='Adj Supplier', visible=True,
        )
        self.product = Product.objects.create(
            name=f'Salt {uuid4().hex[:8]}',
            recipe_code=f'SA{uuid4().hex[:6]}',
            product_class_id=83,
            category_id=83,
            range_id=83,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
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

    def test_requires_trace_and_use_by(self):
        with self.assertRaises(StockAdjustmentError) as ctx:
            receive_stock_adjustment(
                body={
                    'idempotency_key': f'adj-{uuid4()}',
                    'product_id': self.product.id,
                    'location_id': self.wh.id,
                    'quantity': '1',
                    'product_supplier_id': self.mapping.id,
                    'label_format': 'pallet',
                    'label_count': 1,
                    'use_by': (date.today() + timedelta(days=10)).isoformat(),
                },
            )
        self.assertIn('trace_number', str(ctx.exception).lower())

    def test_pack_adjustment_queued(self):
        use_by = (date.today() + timedelta(days=60)).isoformat()
        result = receive_stock_adjustment(
            body={
                'idempotency_key': f'adj-pack-{uuid4()}',
                'product_id': self.product.id,
                'location_id': self.wh.id,
                'product_supplier_id': self.mapping.id,
                'quantity': '1',
                'trace_number': 'ADJ-TRACE-1',
                'use_by': use_by,
                'label_format': 'pallet',
                'label_count': 1,
            },
        )
        self.assertEqual(result['stock_qty'], '10.000000')
        self.assertEqual(result['trace_number'], 'ADJ-TRACE-1')
        self.assertEqual(len(result['receive_results']), 1)
        entry = StockEntry.objects.get(pk=result['stock_entry_id'])
        self.assertEqual(entry.source_document_type, 'stock_adjustment')
        self.assertIsNone(entry.po_number)
        self.assertEqual(result['receive_results'][0]['posting_status'], 'queued')
        self.assertIn('goods_in_label', result['receive_results'][0])

    def test_free_qty_adjustment(self):
        use_by = (date.today() + timedelta(days=30)).isoformat()
        result = receive_stock_adjustment(
            body={
                'idempotency_key': f'adj-free-{uuid4()}',
                'product_id': self.product.id,
                'location_id': self.wh.id,
                'quantity': '3',
                'trace_number': 'ADJ-FREE',
                'use_by': use_by,
                'label_format': 'box',
                'label_count': 1,
                'supplier_id': self.supplier.id,
            },
        )
        self.assertEqual(result['stock_qty'], '3.000000')
        entry = StockEntry.objects.get(pk=result['stock_entry_id'])
        self.assertEqual(entry.quantity, Decimal('3'))
        self.assertEqual(entry.source_document_type, 'stock_adjustment')

