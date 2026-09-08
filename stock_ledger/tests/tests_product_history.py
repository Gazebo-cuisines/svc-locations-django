"""Posted GI/GO product history: no queue, no recon, split boxes collapsed."""

from datetime import date
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
    Range,
    Unit,
)
from stock_ledger.models import StockLot, StockLotOrigin
from stock_ledger.util import entry_posting, services


class ProductMovementHistoryTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=81, name='PH Class')
        Category.objects.create(id=81, name='PH Cat')
        Range.objects.create(id=81, name='PH Range')
        self.unit = Unit.objects.create(id=81, name='Kg')
        self.wh = Location.objects.create(id=81, name='PH WH', visible=True)
        self.dest = Location.objects.create(id=82, name='PH Dest', visible=True)
        self.supplier = Location.objects.create(id=83, name='PH Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'PH {uuid4().hex[:8]}',
            recipe_code=f'PH{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.unit,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        self.client = Client()

    def test_goods_in_omits_queued_and_collapses_posted_boxes(self):
        base = f'ph-box-{uuid4()}'
        ids = []
        for i in range(1, 4):
            entry = services.receipt(
                idempotency_key=f'{base}:u:{i}',
                lot=self.lot,
                location_id=self.wh.id,
                quantity=Decimal('10'),
                unit_id=self.unit.id,
                effective_at=timezone.now(),
                counterparty_location_id=self.supplier.id,
                defer_balance=True,
            )
            entry_posting.queue_entry(entry=entry)
            ids.append(entry.id)
        entry_posting.post_entry(
            entry_id=ids[0], require_label_verified=False,
        )
        entry_posting.post_entry(
            entry_id=ids[1], require_label_verified=False,
        )

        missing = self.client.get('/stock/products/999999/history/goods-in/')
        self.assertEqual(missing.status_code, 404)

        resp = self.client.get(
            f'/stock/products/{self.product.id}/history/goods-in/',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['kind'], 'goods_in')
        grouped = next(r for r in data['items'] if r.get('units'))
        self.assertEqual(grouped['unit_count'], 2)
        self.assertEqual(grouped['quantity'], '20')
        self.assertTrue(grouped['is_live'])
        self.assertEqual(grouped['posting_status'], 'posted')
        self.assertEqual(
            {u['entry_id'] for u in grouped['units']},
            {ids[0], ids[1]},
        )
        item_ids = {r['entry_id'] for r in data['items']}
        self.assertNotIn(ids[2], item_ids)
        for unit in grouped['units']:
            self.assertEqual(unit['posting_status'], 'posted')

    def test_goods_out_skips_transfer_in_recon_and_queued(self):
        services.receipt(
            idempotency_key=f'ph-live-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        out, inn = services.transfer(
            idempotency_key=f'ph-xfer-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        issued = services.issue(
            idempotency_key=f'ph-iss-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        adj = services.count_adjustment(
            idempotency_key=f'ph-adj-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity_delta=Decimal('1'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        queued_out, _queued_in = services.transfer(
            idempotency_key=f'ph-q-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('3'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            defer_balance=True,
        )
        entry_posting.queue_entry(entry=queued_out)

        resp = self.client.get(
            f'/stock/products/{self.product.id}/history/goods-out/',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['kind'], 'goods_out')
        ids = {row['entry_id'] for row in data['items']}
        self.assertIn(out.id, ids)
        self.assertIn(issued.id, ids)
        self.assertNotIn(inn.id, ids)
        self.assertNotIn(adj.id, ids)
        self.assertNotIn(queued_out.id, ids)
        gi = self.client.get(
            f'/stock/products/{self.product.id}/history/goods-in/',
        )
        gi_ids = {row['entry_id'] for row in gi.json()['data']['items']}
        self.assertNotIn(out.id, gi_ids)
        self.assertNotIn(issued.id, gi_ids)

    def test_goods_out_shows_source_sticker_codes(self):
        bag_one = services.receipt(
            idempotency_key=f'ph-bag-one-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        bag_a = services.receipt(
            idempotency_key=f'ph-bag-a-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        bag_b = services.receipt(
            idempotency_key=f'ph-bag-b-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        one, _ = services.transfer(
            idempotency_key=f'ph-one-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            source_entry=bag_one,
        )
        base = f'ph-cart-{uuid4()}'
        services.transfer(
            idempotency_key=f'{base}:l:0',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            source_entry=bag_a,
        )
        services.transfer(
            idempotency_key=f'{base}:l:1',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('20'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            source_entry=bag_b,
        )

        resp = self.client.get(
            f'/stock/products/{self.product.id}/history/goods-out/',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        items = resp.json()['data']['items']
        single = next(r for r in items if r['entry_id'] == one.id)
        self.assertEqual(single['source_entry_code'], f'E{bag_one.id}')
        grouped = next(r for r in items if r.get('unit_count') == 2)
        self.assertEqual(grouped['quantity'], '-25')
        self.assertIsNone(grouped['source_entry_code'])
        by_src = {
            u['source_entry_code']: u['quantity'] for u in grouped['units']
        }
        self.assertEqual(by_src[f'E{bag_a.id}'], '-5')
        self.assertEqual(by_src[f'E{bag_b.id}'], '-20')
