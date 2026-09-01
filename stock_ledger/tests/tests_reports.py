"""Stock report APIs: goods in, goods out, closing stock as-of."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    ProductSupplier,
    Range,
    Unit,
)
from stock_ledger.models import StockEntryPosting, StockEntryPostingStatus, StockLot, StockLotOrigin
from stock_ledger.util import services
from stock_ledger.util.conversions import seed_global_unit_conversions
from users_rbac.models import RbacUser


class StockReportApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=81, name='Rpt Class')
        Category.objects.create(id=81, name='Rpt Cat')
        Range.objects.create(id=81, name='Rpt Range')
        self.unit = Unit.objects.create(id=81, name='Kg')
        self.wh = Location.objects.create(id=81, name='Rpt Warehouse', visible=True)
        self.product = Product.objects.create(
            name=f'Rpt Flour {uuid4().hex[:8]}',
            recipe_code=f'RF{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.unit,
            goods_in_type=ProductGoodsInType.RAW_MATERIAL,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.pack = Product.objects.create(
            name=f'Rpt Film {uuid4().hex[:8]}',
            recipe_code=f'PK{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.unit,
            goods_in_type=ProductGoodsInType.PACKAGING,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )
        self.pack_lot = StockLot.objects.create(
            product=self.pack,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 12, 1),
        )
        self.client = Client()
        tz = timezone.get_current_timezone()
        self.d1 = timezone.make_aware(datetime(2026, 8, 1, 10, 0, 0), tz)
        self.d15 = timezone.make_aware(datetime(2026, 8, 15, 12, 0, 0), tz)
        self.d25 = timezone.make_aware(datetime(2026, 8, 25, 9, 0, 0), tz)

        self.receipt = services.receipt(
            idempotency_key=f'rpt-in-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=self.d1,
        )
        services.receipt(
            idempotency_key=f'rpt-pack-{uuid4()}',
            lot=self.pack_lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=self.d1,
        )
        self.issue = services.issue(
            idempotency_key=f'rpt-out-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('30'),
            unit_id=self.unit.id,
            effective_at=self.d15,
        )
        # Outside goods-in window (still affects closing after 25 Aug).
        services.receipt(
            idempotency_key=f'rpt-late-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=self.d25,
        )

    def test_goods_in_date_range_and_goods_in_type(self):
        resp = self.client.get(
            '/stock/reports/goods-in/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'goods_in_type': 'raw_material',
            },
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['status'], 'success')
        data = body['data']
        self.assertEqual(data['count'], 1)
        row = data['results'][0]
        self.assertEqual(row['entry_id'], self.receipt.id)
        self.assertEqual(row['entry_type'], 'receipt')
        self.assertEqual(row['product_id'], self.product.id)
        self.assertEqual(row['quantity'], '100')

        # Alias product_type
        alias = self.client.get(
            '/stock/reports/goods-in/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'product_type': 'packaging',
            },
        )
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.json()['data']['count'], 1)
        self.assertEqual(
            alias.json()['data']['results'][0]['product_id'],
            self.pack.id,
        )

    def test_goods_out_date_range(self):
        resp = self.client.get(
            '/stock/reports/goods-out/',
            {'date_from': '2026-08-01', 'date_to': '2026-08-20'},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['count'], 1)
        row = data['results'][0]
        self.assertEqual(row['entry_id'], self.issue.id)
        self.assertEqual(row['entry_type'], 'issue')
        self.assertEqual(row['quantity'], '-30')
        self.assertEqual(row['unit_name'], 'Kg')
        self.assertIn('display_kg', row)

    def test_goods_out_includes_transfer_out(self):
        dest = Location.objects.create(id=82, name='Rpt Dest', visible=True)
        out_entry, _in_entry = services.transfer(
            idempotency_key=f'rpt-xfer-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=dest.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=self.d15,
        )
        resp = self.client.get(
            '/stock/reports/goods-out/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'product_id': self.product.id,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {r['entry_id'] for r in resp.json()['data']['results']}
        self.assertIn(self.issue.id, ids)
        self.assertIn(out_entry.id, ids)
        xfer = next(
            r for r in resp.json()['data']['results']
            if r['entry_id'] == out_entry.id
        )
        self.assertEqual(xfer['entry_type'], 'transfer_out')
        self.assertEqual(xfer['quantity'], '-5')

    def test_closing_stock_as_of(self):
        # As of 20 Aug: 100 - 30 = 70 (late +10 not yet)
        before = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-20', 'product_id': self.product.id},
        )
        self.assertEqual(before.status_code, 200, before.content)
        rows = before.json()['data']['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['quantity'], '70')
        self.assertEqual(rows[0]['as_of'], '2026-08-20')

        after = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-25', 'product_id': self.product.id},
        )
        self.assertEqual(after.json()['data']['results'][0]['quantity'], '80')

    def test_closing_excludes_queued(self):
        queued_lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )
        queued = services.receipt(
            idempotency_key=f'rpt-q-{uuid4()}',
            lot=queued_lot,
            location_id=self.wh.id,
            quantity=Decimal('999'),
            unit_id=self.unit.id,
            effective_at=self.d1,
            defer_balance=True,
        )
        StockEntryPosting.objects.create(
            stock_entry=queued,
            status=StockEntryPostingStatus.QUEUED,
            queued_at=timezone.now(),
        )
        resp = self.client.get(
            '/stock/reports/goods-in/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'product_id': self.product.id,
            },
        )
        ids = [r['entry_id'] for r in resp.json()['data']['results']]
        self.assertNotIn(queued.id, ids)

        close = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-20', 'product_id': self.product.id},
        )
        # Still 70 — queued 999 ignored
        self.assertEqual(close.json()['data']['results'][0]['quantity'], '70')

    def test_missing_dates_400(self):
        self.assertEqual(
            self.client.get('/stock/reports/goods-in/').status_code,
            400,
        )
        self.assertEqual(
            self.client.get('/stock/reports/closing-stock/').status_code,
            400,
        )
        bad = self.client.get(
            '/stock/reports/goods-in/',
            {'date_from': '2026-08-20', 'date_to': '2026-08-01'},
        )
        self.assertEqual(bad.status_code, 400)

    def test_operator_activity_summary_and_detail(self):
        user = RbacUser.objects.create(
            cognito_sub=f'sub-{uuid4().hex[:12]}',
            username=f'op-{uuid4().hex[:8]}',
            email=f'op-{uuid4().hex[:8]}@example.com',
            display_name='Ops Tester',
        )
        self.receipt.actor_user_id = user.id
        self.receipt.recorded_at = self.d1
        self.receipt.save(update_fields=['actor_user_id', 'recorded_at'])

        resp = self.client.get(
            '/stock/reports/operator-activity/',
            {'date': '2026-08-01', 'from_time': '09:00', 'to_time': '18:00'},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['summary']['entries'] >= 1, True)
        self.assertEqual(data['summary']['users'], 1)
        op = next(r for r in data['operators'] if r['user_id'] == user.id)
        self.assertEqual(op['display_name'], 'Ops Tester')
        self.assertGreaterEqual(op['entries'], 1)

        detail = self.client.get(
            '/stock/reports/operator-activity/detail/',
            {'date': '2026-08-01', 'user_id': user.id},
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        body = detail.json()['data']
        self.assertEqual(body['user_id'], user.id)
        self.assertGreaterEqual(body['count'], 1)
        self.assertEqual(body['results'][0]['entry_id'], self.receipt.id)

    def test_operator_activity_requires_date(self):
        self.assertEqual(
            self.client.get('/stock/reports/operator-activity/').status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                '/stock/reports/operator-activity/detail/',
                {'date': '2026-08-01'},
            ).status_code,
            400,
        )

    def test_reports_include_supplier_pack_fields(self):
        seed_global_unit_conversions()
        box = Unit.objects.create(id=82, name='Box')
        supplier = Location.objects.create(id=83, name='Rpt Sup', visible=True)
        mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=supplier,
            supplier_code='CST-FLOUR-25',
            sage_product_code='SAGE-FLOUR-25',
            supplier_product_name='Flour 25kg',
            outer_qty=Decimal('1'),
            outer_unit=box,
            inner_qty=Decimal('25'),
            inner_unit=self.unit,
            is_default=True,
            is_active=True,
        )
        pack_lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )
        receipt = services.receipt(
            idempotency_key=f'rpt-pack-shape-{uuid4()}',
            lot=pack_lot,
            location_id=self.wh.id,
            quantity=Decimal('2'),
            product_supplier=mapping,
            effective_at=self.d1,
            counterparty_location_id=supplier.id,
        )
        services.issue(
            idempotency_key=f'rpt-pack-out-{uuid4()}',
            lot=pack_lot,
            location_id=self.wh.id,
            quantity=Decimal('25'),
            unit_id=self.unit.id,
            effective_at=self.d15,
        )

        goods_in = self.client.get(
            '/stock/reports/goods-in/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'product_id': self.product.id,
            },
        )
        self.assertEqual(goods_in.status_code, 200, goods_in.content)
        in_row = next(
            r for r in goods_in.json()['data']['results']
            if r['entry_id'] == receipt.id
        )
        self.assertEqual(in_row['sage_product_code'], 'SAGE-FLOUR-25')
        self.assertEqual(in_row['supplier_code'], 'CST-FLOUR-25')
        self.assertEqual(Decimal(in_row['pack_quantity']), Decimal('2'))
        self.assertEqual(in_row['pack_unit_name'], 'Box')
        self.assertEqual(Decimal(in_row['display_kg']), Decimal('50'))
        self.assertTrue(in_row['shape_format_label'])

        goods_out = self.client.get(
            '/stock/reports/goods-out/',
            {
                'date_from': '2026-08-01',
                'date_to': '2026-08-20',
                'product_id': self.product.id,
            },
        )
        out_row = next(
            r for r in goods_out.json()['data']['results']
            if r['lot_id'] == pack_lot.id
        )
        self.assertEqual(out_row['sage_product_code'], 'SAGE-FLOUR-25')
        self.assertEqual(Decimal(out_row['pack_quantity']), Decimal('1'))
        self.assertEqual(Decimal(out_row['display_kg']), Decimal('25'))

        close = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-20', 'product_id': self.product.id},
        )
        close_data = close.json()['data']
        self.assertEqual(close_data['view'], 'detail')
        close_row = next(
            r for r in close_data['results']
            if r['lot_id'] == pack_lot.id
        )
        self.assertEqual(close_row['quantity'], '25')
        self.assertEqual(close_row['use_by'], '2026-09-01')
        self.assertEqual(close_row['production_date'], '2026-08-01')
        self.assertEqual(close_row['sage_product_code'], 'SAGE-FLOUR-25')
        self.assertEqual(Decimal(close_row['pack_quantity']), Decimal('1'))
        self.assertEqual(close_row['pack_unit_name'], 'Box')
        self.assertEqual(Decimal(close_row['display_kg']), Decimal('25'))
        self.assertTrue(close_row['shape_format_label'])

        # Lot without stamped mapping still picks default product_supplier.
        default_close = next(
            r for r in close_data['results']
            if r['lot_id'] == self.lot.id
        )
        self.assertEqual(default_close['sage_product_code'], 'SAGE-FLOUR-25')
        self.assertEqual(default_close['supplier_code'], 'CST-FLOUR-25')
        self.assertIsNotNone(default_close['pack_quantity'])

        # Second lot, earlier use-by — consolidated rolls both into one shape row.
        early_lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 8, 20),
        )
        services.receipt(
            idempotency_key=f'rpt-pack-early-{uuid4()}',
            lot=early_lot,
            location_id=self.wh.id,
            quantity=Decimal('1'),
            product_supplier=mapping,
            effective_at=self.d1,
            counterparty_location_id=supplier.id,
        )
        consol = self.client.get(
            '/stock/reports/closing-stock/',
            {
                'as_of': '2026-08-20',
                'product_id': self.product.id,
                'view': 'consolidated',
            },
        )
        self.assertEqual(consol.status_code, 200, consol.content)
        body = consol.json()['data']
        self.assertEqual(body['view'], 'consolidated')
        self.assertEqual(body['group_by'], 'product_shape')
        consol_row = next(
            r for r in body['results']
            if r['product_supplier_id'] == mapping.id
        )
        detail = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-20', 'product_id': self.product.id},
        ).json()['data']['results']
        expected_qty = sum(
            Decimal(r['quantity'])
            for r in detail
            if r.get('product_supplier_id') == mapping.id
        )
        self.assertEqual(Decimal(consol_row['quantity']), expected_qty)
        self.assertEqual(
            Decimal(consol_row['pack_quantity']),
            expected_qty / Decimal('25'),
        )
        self.assertEqual(consol_row['earliest_use_by'], '2026-08-20')
        self.assertEqual(consol_row['latest_use_by'], '2026-09-01')
        self.assertEqual(consol_row['earliest_production_date'], '2026-08-01')
        self.assertEqual(consol_row['latest_production_date'], '2026-08-01')
        self.assertGreaterEqual(consol_row['lot_count'], 2)
        self.assertEqual(consol_row['sage_product_code'], 'SAGE-FLOUR-25')
        self.assertNotIn('lot_id', consol_row)
        self.assertNotIn('trace_number', consol_row)
        self.assertNotIn('use_by', consol_row)

        bad_view = self.client.get(
            '/stock/reports/closing-stock/',
            {'as_of': '2026-08-20', 'view': 'nope'},
        )
        self.assertEqual(bad_view.status_code, 400)
