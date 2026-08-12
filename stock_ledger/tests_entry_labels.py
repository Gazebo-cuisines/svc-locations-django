"""Goods IN entry barcode (E{id}) + print/verify."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductLabelMode,
    Range,
    Unit,
)
from stock_ledger.models import (
    StockEntryLabelStatus,
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
)
from stock_ledger.util import entry_labels, services


class EntryLabelTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=71, name='EL Class')
        Category.objects.create(id=71, name='EL Cat')
        Range.objects.create(id=71, name='EL Range')
        self.unit = Unit.objects.create(id=71, name='Kg')
        self.wh = Location.objects.create(id=71, name='EL WH', visible=True)
        self.product = Product.objects.create(
            name=f'EL Chicken {uuid4().hex[:8]}',
            recipe_code=f'EL{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
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
        self.entry = services.receipt(
            idempotency_key=f'el-receipt-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('500'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()

    def test_pallet_label_scan_print_verify(self):
        label = entry_labels.create_entry_label(
            entry=self.entry,
            label_format='pallet',
        )
        self.assertEqual(label.label_count, 1)
        payload = entry_labels.build_goods_in_label(self.entry, label)
        self.assertEqual(payload['title'], 'Goods IN')
        self.assertEqual(payload['barcode'], f'E{self.entry.id}')
        self.assertEqual(payload['size_mm'], {'width_mm': 100, 'height_mm': 150})
        self.assertEqual(payload['trace_number'], self.lot.trace_number)

        scan = self.client.get(f'/stock/scan/?code=E{self.entry.id}')
        self.assertEqual(scan.status_code, 200)
        data = scan.json()['data']
        self.assertEqual(data['match_type'], 'entry')
        self.assertEqual(data['entry']['id'], self.entry.id)
        self.assertEqual(data['goods_in_label']['barcode'], f'E{self.entry.id}')

        printed = self.client.post(
            f'/stock/entries/{self.entry.id}/labels/print/',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(printed.status_code, 200)
        self.assertEqual(
            printed.json()['data']['label']['status'],
            StockEntryLabelStatus.PRINTED,
        )

        bad = self.client.post(
            f'/stock/entries/{self.entry.id}/labels/verify/',
            data='{"code":"E999999"}',
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400)

        ok = self.client.post(
            f'/stock/entries/{self.entry.id}/labels/verify/',
            data=f'{{"code":"E{self.entry.id}"}}',
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(
            ok.json()['data']['label']['status'],
            StockEntryLabelStatus.VERIFIED,
        )

    def test_box_requires_count_and_multi_verify(self):
        with self.assertRaises(Exception):
            entry_labels.create_entry_label(
                entry=self.entry,
                label_format='box',
            )
        label = entry_labels.create_entry_label(
            entry=self.entry,
            label_format='box',
            label_count=3,
        )
        payload = entry_labels.build_goods_in_label(self.entry, label)
        self.assertEqual(payload['copies'], 3)
        self.assertEqual(payload['size_mm'], {'width_mm': 40, 'height_mm': 30})

        code = f'E{self.entry.id}'
        for i in range(3):
            result = entry_labels.verify_label(entry_id=self.entry.id, code=code)
            if i < 2:
                self.assertEqual(result['label']['status'], StockEntryLabelStatus.PRINTED)
            else:
                self.assertEqual(result['label']['status'], StockEntryLabelStatus.VERIFIED)
                self.assertEqual(result['label']['verified_count'], 3)

    def test_issue_from_source_entry_returns_goods_out_label(self):
        entry_labels.create_entry_label(
            entry=self.entry,
            label_format='pallet',
        )
        resp = self.client.post(
            '/stock/issue/',
            data=(
                '{'
                f'"idempotency_key":"el-issue-{uuid4()}",'
                f'"source_entry_id":{self.entry.id},'
                '"quantity":"50",'
                '"goods_out_label_count":5'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['source_entry_code'], f'E{self.entry.id}')
        out = data['goods_out_label']
        self.assertEqual(out['title'], 'Goods OUT')
        self.assertEqual(out['copies'], 5)
        self.assertEqual(out['source_entry_code'], f'E{self.entry.id}')
        self.assertTrue(out['barcode'].startswith('E'))
