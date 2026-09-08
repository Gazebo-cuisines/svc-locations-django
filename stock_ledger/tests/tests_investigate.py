"""GET /stock/investigate/: remaining, cancel vs post, briefing."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from core.ai_tools import TOOLS, openapi_schema, run_tool
from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import StockEntry, StockLot, StockLotOrigin
from stock_ledger.util import entry_posting, services  # noqa: F401


class InvestigateApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=81, name='Inv Class')
        Category.objects.create(id=81, name='Inv Cat')
        Range.objects.create(id=81, name='Inv Range')
        self.unit = Unit.objects.create(id=81, name='Kg')
        self.wh = Location.objects.create(id=81, name='Inv WH', visible=True)
        self.kitchen = Location.objects.create(id=82, name='Inv Kitchen', visible=True)
        self.product = Product.objects.create(
            name=f'Inv Powder {uuid4().hex[:8]}',
            recipe_code=f'INV{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
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
        self.bag = services.receipt(
            idempotency_key=f'inv-in-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('25'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.other = services.receipt(
            idempotency_key=f'inv-in2-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()

    def _queue(self, source, qty):
        resp = self.client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'inv-xfer-{uuid4()}',
                'lot_id': self.lot.id,
                'from_location_id': self.wh.id,
                'quantity': str(qty),
                'unit_id': self.unit.id,
                'queue_stock': True,
                'source_entry_id': source.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()['data']['out']['id']

    def _get(self, **params):
        q = '&'.join(f'{k}={v}' for k, v in params.items() if v is not None)
        return self.client.get(f'/stock/investigate/?{q}')

    def test_unknown_code_404(self):
        resp = self._get(code='E99999999')
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_queued_full_bag_remaining_zero(self):
        out_id = self._queue(self.bag, 25)
        resp = self._get(code=f'E{self.bag.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['bag']['remaining'], '0')
        self.assertEqual(data['decision']['kind'], 'reserved_by_queue')
        self.assertEqual(data['decision']['queued_draw_code'], f'E{out_id}')
        briefing = data['briefing']
        self.assertIn(f'E{self.bag.id}', briefing)
        self.assertIn('Goods out without plan', briefing)
        self.assertIn('no stock in bag', briefing)
        self.assertIn(f'E{out_id}', briefing)
        self.assertIn('goods_out_adhoc', briefing)

    def test_cancel_restores_remaining(self):
        out_id = self._queue(self.bag, 25)
        cancel = self.client.post(f'/stock/entries/{out_id}/cancel/')
        self.assertEqual(cancel.status_code, 200, cancel.content)
        resp = self._get(code=f'E{self.bag.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['bag']['remaining'], '25')
        self.assertEqual(data['decision']['kind'], 'bag_has_stock')
        self.assertIn('Cancelled', data['briefing'])
        self.assertIn('Bag qty back to 25 Kg', data['briefing'])

    def test_posted_bag_empty_next_sticker(self):
        out_id = self._queue(self.bag, 25)
        ok = self.client.post(
            f'/stock/entries/{out_id}/labels/verify/',
            data=f'{{"code":"E{out_id}","post_stock":true}}',
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        resp = self._get(code=f'E{self.bag.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['bag']['remaining'], '0')
        self.assertEqual(data['decision']['kind'], 'bag_empty')
        self.assertIn(f'E{self.other.id}', data['decision']['use_next_bag'])
        briefing = data['briefing']
        self.assertIn('is empty now', briefing)
        self.assertIn('is not missing', briefing)
        self.assertIn(f'E{self.other.id}', briefing)
        self.assertIn(f'E{out_id}', briefing)

    def test_recipe_code_finds_product(self):
        resp = self._get(recipe_code=self.product.recipe_code)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['product']['product_id'], self.product.id)
        self.assertEqual(data['bag']['entry_id'], self.bag.id)

    def test_run_tool_and_schema(self):
        self.assertIn('/stock/investigate/', TOOLS)
        self.assertIn('/stock/scan/goods-out/', TOOLS)
        self.assertIn('/stock/entries/{pk}/', TOOLS)
        self.assertIn('/stock/products/{product_id}/history/goods-out/', TOOLS)
        schema = openapi_schema()
        self.assertEqual(set(schema['paths']), set(TOOLS))
        body = run_tool('/stock/investigate/', {'code': f'E{self.bag.id}'})
        self.assertEqual(body['status'], 'success')
        self.assertIn('briefing', body['data'])
        entry = run_tool('/stock/entries/{pk}/', {'pk': self.bag.id})
        self.assertEqual(entry['status'], 'success')
        hist = run_tool(
            '/stock/products/{product_id}/history/goods-out/',
            {'product_id': self.product.id},
        )
        self.assertEqual(hist['status'], 'success')
        scan = run_tool(
            '/stock/scan/goods-out/',
            {'code': f'E{self.bag.id}', 'location_id': self.wh.id},
        )
        self.assertEqual(scan['status'], 'success')
        self.assertEqual(scan['data']['quantity'], '25')

    def test_product_dossier_goods_in_out_reversal_reason(self):
        out_id = self._queue(self.bag, 25)
        ok = self.client.post(
            f'/stock/entries/{out_id}/labels/verify/',
            data=f'{{"code":"E{out_id}","post_stock":true}}',
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        out = StockEntry.objects.get(pk=out_id)
        rev = services.reversal(
            idempotency_key=f'inv-rev-{uuid4()}',
            entry=out,
            remarks='Wrong bag scanned',
            lan_username='HARVI',
        )
        resp = self._get(recipe_code=self.product.recipe_code)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        gi = [r['entry_code'] for r in data['goods_in']]
        self.assertIn(f'E{self.bag.id}', gi)
        go = next(r for r in data['goods_out'] if r['entry_id'] == out_id)
        self.assertEqual(go['source_bag'], f'E{self.bag.id}')
        self.assertEqual(go['reversed_by'], f'E{rev.id}')
        row = next(r for r in data['reversals'] if r['entry_id'] == rev.id)
        self.assertEqual(row['actor'], 'HARVI')
        self.assertEqual(row['reason'], 'Wrong bag scanned')
        self.assertEqual(row['reverses'], f'E{out_id}')
        briefing = data['briefing']
        self.assertIn('## Goods in', briefing)
        self.assertIn('## Goods out', briefing)
        self.assertIn('Wrong bag scanned', briefing)
        self.assertIn('HARVI', briefing)
