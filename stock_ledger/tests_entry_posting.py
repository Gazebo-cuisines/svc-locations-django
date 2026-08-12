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
