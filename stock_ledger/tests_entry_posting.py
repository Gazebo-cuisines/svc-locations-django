"""Queued goods-in: balance only after label verify + post."""

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
from stock_ledger.models import (
    StockBalance,
    StockEntryLabelStatus,
    StockEntryPostingStatus,
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
)
from stock_ledger.util import entry_labels, entry_posting, services


class EntryPostingQueueTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=72, name='PQ Class')
        Category.objects.create(id=72, name='PQ Cat')
        Range.objects.create(id=72, name='PQ Range')
        self.unit = Unit.objects.create(id=72, name='Kg')
        self.wh = Location.objects.create(id=72, name='PQ WH', visible=True)
        self.supplier = Location.objects.create(id=73, name='PQ Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'PQ Chicken {uuid4().hex[:8]}',
            recipe_code=f'PQ{uuid4().hex[:6]}',
            product_class_id=72,
            category_id=72,
            range_id=72,
            unit=self.unit,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        StockPeriod.objects.get_or_create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            defaults={'status': StockPeriodStatus.OPEN},
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        self.client = Client()

    def test_queued_receipt_hidden_until_post(self):
        resp = self.client.post(
            '/stock/receipt/',
            data=(
                '{'
                f'"idempotency_key":"pq-{uuid4()}",'
                f'"product_id":{self.product.id},'
                f'"location_id":{self.wh.id},'
                f'"supplier_id":{self.supplier.id},'
                '"quantity":"100",'
                f'"trace_number":"{self.lot.trace_number}",'
                f'"use_by":"{self.lot.use_by.isoformat()}",'
                '"label_format":"pallet"'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        entry_id = data['id']
        self.assertEqual(data['posting_status'], StockEntryPostingStatus.QUEUED)
        self.assertFalse(
            StockBalance.objects.filter(
                lot_id=data['lot_id'], location_id=self.wh.id,
            ).exists(),
        )

        queued = self.client.get('/stock/entries/queued/')
        self.assertEqual(queued.status_code, 200)
        self.assertGreaterEqual(queued.json()['data']['count'], 1)

        # Post blocked until label verified.
        blocked = self.client.post(
            f'/stock/entries/{entry_id}/post/',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 400)

        verify = self.client.post(
            f'/stock/entries/{entry_id}/labels/verify/',
            data=f'{{"code":"E{entry_id}","post_stock":true}}',
            content_type='application/json',
        )
        self.assertEqual(verify.status_code, 200, verify.content)
        vdata = verify.json()['data']
        self.assertEqual(vdata['label']['status'], StockEntryLabelStatus.VERIFIED)
        self.assertEqual(vdata['posting_status'], StockEntryPostingStatus.POSTED)

        bal = StockBalance.objects.get(lot_id=data['lot_id'], location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('100'))

        # Idempotent re-post.
        again = entry_posting.post_entry(entry_id=entry_id)
        self.assertTrue(again['already_live'])
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=data['lot_id'], location_id=self.wh.id,
            ).quantity,
            Decimal('100'),
        )

    def test_queued_transfer_returns_goods_out_label_without_moving_stock(self):
        dest = Location.objects.create(id=74, name='PQ Belts', visible=True)
        services.receipt(
            idempotency_key=f'pq-live-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        before = StockBalance.objects.get(
            lot_id=self.lot.id, location_id=self.wh.id,
        ).quantity
        resp = self.client.post(
            '/stock/transfer/',
            data=(
                '{'
                f'"idempotency_key":"pq-xfer-{uuid4()}",'
                f'"lot_id":{self.lot.id},'
                f'"from_location_id":{self.wh.id},'
                f'"to_location_id":{dest.id},'
                '"quantity":"10",'
                '"queue_stock":true'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['message'], 'Transfer queued.')
        data = resp.json()['data']
        out_id = data['out']['id']
        label = data['goods_out_label']
        self.assertEqual(label['title'], 'Goods OUT')
        self.assertEqual(label['barcode'], f'E{out_id}')
        self.assertEqual(label['trace_number'], self.lot.trace_number)
        self.assertEqual(data['posting']['status'], StockEntryPostingStatus.QUEUED)
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).quantity,
            before,
        )
        self.assertFalse(
            StockBalance.objects.filter(
                lot_id=self.lot.id, location_id=dest.id,
            ).exists(),
        )

    def test_verify_posts_queued_transfer_pair(self):
        dest = Location.objects.create(id=75, name='PQ Mixers', visible=True)
        services.receipt(
            idempotency_key=f'pq-live2-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        queued = self.client.post(
            '/stock/transfer/',
            data=(
                '{'
                f'"idempotency_key":"pq-xfer2-{uuid4()}",'
                f'"lot_id":{self.lot.id},'
                f'"from_location_id":{self.wh.id},'
                f'"to_location_id":{dest.id},'
                '"quantity":"10",'
                '"queue_stock":true'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(queued.status_code, 201, queued.content)
        out_id = queued.json()['data']['out']['id']

        bad = self.client.post(
            f'/stock/entries/{out_id}/labels/verify/',
            data='{"code":"E999","post_stock":true}',
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).quantity,
            Decimal('50'),
        )

        ok = self.client.post(
            f'/stock/entries/{out_id}/labels/verify/',
            data=f'{{"code":"E{out_id}","post_stock":true}}',
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        vdata = ok.json()['data']
        self.assertEqual(vdata['label']['status'], StockEntryLabelStatus.VERIFIED)
        self.assertEqual(vdata['posting_status'], StockEntryPostingStatus.POSTED)
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=self.wh.id,
            ).quantity,
            Decimal('40'),
        )
        self.assertEqual(
            StockBalance.objects.get(
                lot_id=self.lot.id, location_id=dest.id,
            ).quantity,
            Decimal('10'),
        )

    def test_fifo_override_required_and_listed_on_product(self):
        dest = Location.objects.create(id=76, name='PQ Belts 2', visible=True)
        soon = StockLot.objects.create(
            product=self.product,
            trace_number=f'TSOON{uuid4().hex[:6]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 9, 1),
        )
        later = StockLot.objects.create(
            product=self.product,
            trace_number=f'TLATE{uuid4().hex[:6]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        for lot in (soon, later):
            services.receipt(
                idempotency_key=f'pq-ov-{lot.id}-{uuid4()}',
                lot=lot,
                location_id=self.wh.id,
                quantity=Decimal('20'),
                unit_id=self.unit.id,
                effective_at=timezone.now(),
                counterparty_location_id=self.supplier.id,
            )
        denied = self.client.post(
            '/stock/transfer/',
            data=(
                '{'
                f'"idempotency_key":"pq-ov-deny-{uuid4()}",'
                f'"lot_id":{later.id},'
                f'"from_location_id":{self.wh.id},'
                f'"to_location_id":{dest.id},'
                '"quantity":"5",'
                '"queue_stock":true'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(denied.status_code, 400, denied.content)
        self.assertIn('fifo_override_reason', denied.json()['message'])

        ok = self.client.post(
            '/stock/transfer/',
            data=(
                '{'
                f'"idempotency_key":"pq-ov-ok-{uuid4()}",'
                f'"lot_id":{later.id},'
                f'"from_location_id":{self.wh.id},'
                f'"to_location_id":{dest.id},'
                '"quantity":"5",'
                '"queue_stock":true,'
                '"fifo_override_reason":"old stock at the back"'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 201, ok.content)
        listed = self.client.get(
            f'/product/{self.product.id}/stock-overrides/',
        )
        self.assertEqual(listed.status_code, 200, listed.content)
        items = listed.json()['data']['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['scanned_trace'], later.trace_number)
        self.assertEqual(items[0]['recommended_trace'], soon.trace_number)
        self.assertEqual(items[0]['reason'], 'old stock at the back')
