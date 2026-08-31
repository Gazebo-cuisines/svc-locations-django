"""Goods Out without plan: scan dest fields + transfer auto to_location."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import StockEntry, StockLot, StockLotOrigin
from stock_ledger.util import services


class GoodsOutWithoutPlanTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=71, name='GO Class')
        Category.objects.create(id=71, name='GO Cat')
        Range.objects.create(id=71, name='GO Range')
        self.unit = Unit.objects.create(id=71, name='Kg')
        self.wh = Location.objects.create(id=71, name='GO WH', visible=True)
        self.kitchen = Location.objects.create(id=72, name='GO Kitchen', visible=True)
        self.product = Product.objects.create(
            name=f'GO Peas {uuid4().hex[:8]}',
            recipe_code=f'GP{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.kitchen,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date.today() + timedelta(days=20),
        )
        self.entry = services.receipt(
            idempotency_key=f'go-in-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('25'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()

    def test_scan_goods_out_includes_auto_destination(self):
        resp = self.client.get(
            f'/stock/scan/goods-out/?code=E{self.entry.id}'
            f'&location_id={self.wh.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertTrue(data['fifo_ok'])
        self.assertTrue(data['dest_ok'])
        self.assertEqual(data['to_location_id'], self.kitchen.id)
        self.assertEqual(data['to_location_name'], 'GO Kitchen')
        self.assertEqual(
            data['product']['destination_container_id'],
            self.kitchen.id,
        )

    def test_transfer_auto_to_location_without_requirement_ids(self):
        resp = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-xfer-{uuid4()}',
                'lot_id': self.lot.id,
                'from_location_id': self.wh.id,
                # omit to_location_id → product.destination_container
                'quantity': '25',
                'unit_id': self.unit.id,
                'queue_stock': True,
                'source_entry_id': self.entry.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()['data']
        self.assertIn('goods_out_label', body)
        out = body['out']
        self.assertEqual(out['location_id'], self.wh.id)
        self.assertEqual(out['counterparty_location_id'], self.kitchen.id)
        self.assertEqual(out['source_document_type'], 'goods_out_adhoc')
        self.assertIsNone(out.get('source_document_id'))

    def test_transfer_blocks_when_product_has_no_destination(self):
        from unittest.mock import PropertyMock, patch

        with patch.object(
            Product,
            'destination_container_id',
            new_callable=PropertyMock,
            return_value=None,
        ):
            resp = self.client.post(
                '/stock/transfer/',
                data={
                    'idempotency_key': f'go-xfer-bad-{uuid4()}',
                    'lot_id': self.lot.id,
                    'from_location_id': self.wh.id,
                    'quantity': '5',
                    'unit_id': self.unit.id,
                    'queue_stock': True,
                },
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('destination location', resp.json()['message'].lower())
