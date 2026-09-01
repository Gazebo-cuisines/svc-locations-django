"""Goods Out without plan: scan dest fields + transfer auto to_location."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import (
    StockBalance,
    StockEntryPostingStatus,
    StockLot,
    StockLotOrigin,
)
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

    def test_transfer_label_count_creates_n_out_barcodes(self):
        resp = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-multi-{uuid4()}',
                'lot_id': self.lot.id,
                'from_location_id': self.wh.id,
                'quantity': '15',
                'unit_id': self.unit.id,
                'queue_stock': True,
                'label_format': 'box',
                'label_count': 3,
                'source_entry_id': self.entry.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()['data']
        self.assertEqual(body['label_count'], 3)
        self.assertEqual(body['transaction_count'], 3)
        self.assertEqual(len(body['transactions']), 3)
        barcodes = [tx['goods_out_label']['barcode'] for tx in body['transactions']]
        self.assertEqual(len(set(barcodes)), 3)
        self.assertEqual(body['goods_out_label']['barcode'], barcodes[0])
        qtys = [abs(Decimal(tx['out']['quantity'])) for tx in body['transactions']]
        self.assertEqual(sum(qtys), Decimal('15'))
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).quantity,
            Decimal('25'),
        )
        for tx in body['transactions']:
            self.assertEqual(
                tx['posting']['status'], StockEntryPostingStatus.QUEUED,
            )
            out_id = tx['out']['id']
            self.assertEqual(tx['goods_out_label']['barcode'], f'E{out_id}')

    def test_transfer_label_count_verify_each_posts_stock(self):
        queued = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-multi-post-{uuid4()}',
                'lot_id': self.lot.id,
                'from_location_id': self.wh.id,
                'quantity': '12',
                'unit_id': self.unit.id,
                'queue_stock': True,
                'label_format': 'box',
                'label_count': 3,
                'source_entry_id': self.entry.id,
            },
            content_type='application/json',
        )
        self.assertEqual(queued.status_code, 201, queued.content)
        txs = queued.json()['data']['transactions']
        for tx in txs:
            out_id = tx['out']['id']
            ok = self.client.post(
                f'/stock/entries/{out_id}/labels/verify/',
                data=f'{{"code":"E{out_id}","post_stock":true}}',
                content_type='application/json',
            )
            self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).quantity,
            Decimal('13'),
        )
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.kitchen.id,
            ).quantity,
            Decimal('12'),
        )

    def test_suggest_fifo_stickers_covers_required_kg(self):
        older = StockLot.objects.create(
            product=self.product,
            trace_number=f'TOLD{uuid4().hex[:6]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 7, 1),
            use_by=date.today() + timedelta(days=5),
        )
        e1 = services.receipt(
            idempotency_key=f'go-sug-1-{uuid4()}',
            lot=older,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        e2 = services.receipt(
            idempotency_key=f'go-sug-2-{uuid4()}',
            lot=older,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        newer = services.receipt(
            idempotency_key=f'go-sug-new-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        resp = self.client.get(
            f'/stock/goods-out/suggest/?product_id={self.product.id}'
            f'&location_id={self.wh.id}&quantity=20',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['required_quantity'], '20')
        self.assertEqual(data['suggested_quantity'], '20')
        self.assertEqual(data['shortfall'], '0')
        self.assertEqual(data['pick_count'], 2)
        self.assertEqual(
            [p['entry_id'] for p in data['picks']],
            [e1.id, e2.id],
        )
        self.assertNotIn(newer.id, [p['entry_id'] for p in data['picks']])

    def test_suggest_reports_shortfall(self):
        resp = self.client.get(
            f'/stock/goods-out/suggest/?product_id={self.product.id}'
            f'&location_id={self.wh.id}&quantity=100',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['suggested_quantity'], '25')
        self.assertEqual(data['shortfall'], '75')

    def test_transfer_lines_queues_one_out_per_sticker(self):
        e2 = services.receipt(
            idempotency_key=f'go-line-2-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('15'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        resp = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-lines-{uuid4()}',
                'from_location_id': self.wh.id,
                'queue_stock': True,
                'required_quantity': '40',
                'lines': [
                    {
                        'lot_id': self.lot.id,
                        'quantity': '25',
                        'source_entry_id': self.entry.id,
                    },
                    {
                        'lot_id': self.lot.id,
                        'quantity': '15',
                        'source_entry_id': e2.id,
                    },
                ],
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()['data']
        self.assertEqual(body['transaction_count'], 2)
        self.assertEqual(body['required_quantity'], '40')
        barcodes = [tx['goods_out_label']['barcode'] for tx in body['transactions']]
        self.assertEqual(len(set(barcodes)), 2)
        for tx in body['transactions']:
            out_id = tx['out']['id']
            ok = self.client.post(
                f'/stock/entries/{out_id}/labels/verify/',
                data=f'{{"code":"E{out_id}","post_stock":true}}',
                content_type='application/json',
            )
            self.assertEqual(ok.status_code, 200, ok.content)
        self.assertFalse(
            StockBalance.objects.filter(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).exists(),
        )
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.kitchen.id,
            ).quantity,
            Decimal('40'),
        )

    def test_transfer_lines_requires_fifo_override(self):
        older = StockLot.objects.create(
            product=self.product,
            trace_number=f'TFIFO{uuid4().hex[:6]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 7, 1),
            use_by=date.today() + timedelta(days=3),
        )
        services.receipt(
            idempotency_key=f'go-fifo-old-{uuid4()}',
            lot=older,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        bad = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-lines-bad-{uuid4()}',
                'from_location_id': self.wh.id,
                'queue_stock': True,
                'lines': [
                    {
                        'lot_id': self.lot.id,
                        'quantity': '10',
                        'source_entry_id': self.entry.id,
                    },
                ],
            },
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertIn('fifo_override_reason', bad.json()['message'])
        ok = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-lines-ok-{uuid4()}',
                'from_location_id': self.wh.id,
                'queue_stock': True,
                'fifo_override_reason': 'damaged oldest',
                'lines': [
                    {
                        'lot_id': self.lot.id,
                        'quantity': '10',
                        'source_entry_id': self.entry.id,
                    },
                ],
            },
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 201, ok.content)

    def test_transfer_lines_rejects_label_count(self):
        resp = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'go-mix-{uuid4()}',
                'from_location_id': self.wh.id,
                'queue_stock': True,
                'label_format': 'box',
                'label_count': 2,
                'lines': [
                    {
                        'lot_id': self.lot.id,
                        'quantity': '10',
                        'source_entry_id': self.entry.id,
                    },
                ],
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('lines cannot be combined', resp.json()['message'])
