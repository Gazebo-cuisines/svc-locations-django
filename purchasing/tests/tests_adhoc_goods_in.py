"""Without-PO goods-in QC + receive (Chunks 2–3)."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase

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
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import AdhocGoodsInStatus
from purchasing.services.adhoc_goods_in import (
    AdhocGoodsInError,
    receive_adhoc_goods_in,
    start_adhoc_goods_in,
    submit_adhoc_header_qc,
    submit_adhoc_line_qc,
)
from purchasing.services.julian import julian_trace_number
from stock_ledger.models import StockEntry


class AdhocGoodsInQcTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=81, name='Adhoc Class')
        Category.objects.create(id=81, name='Adhoc Cat')
        Range.objects.create(id=81, name='Adhoc Range')
        self.kg = Unit.objects.create(id=81, name='Kg')
        self.bag = Unit.objects.create(id=82, name='Bag')
        self.wh = Location.objects.create(id=81, name='Adhoc WH', visible=True)
        self.supplier = Location.objects.create(id=82, name='Adhoc Supplier', visible=True)
        self.product = Product.objects.create(
            name=f'Turmeric {uuid4().hex[:8]}',
            recipe_code=f'TU{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.food = Product.objects.create(
            name=f'Chicken {uuid4().hex[:8]}',
            recipe_code=f'CK{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.RAW_MATERIAL,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='TURM-1X5',
            supplier_product_name='Turmeric 1x5kg',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('5'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )

    def _qc_complete(self, product=None):
        product = product or self.product
        form = start_adhoc_goods_in(
            product_id=product.id,
            location_id=self.wh.id,
            created_by_user_id=7,
        )
        sid = form['session_id']
        if product.goods_in_type == ProductGoodsInType.RAW_MATERIAL:
            submit_adhoc_header_qc(
                sid,
                body={
                    'checked_by_user_id': 7,
                    'delivery_date': date.today().isoformat(),
                    'answers': {
                        'vehicle_clean_fb_pest_odour': {'value': True},
                        'primary_outer_packaging_damaged': {'value': False},
                        'reject_delivery': {'value': False},
                    },
                },
            )
            use_by = (date.today() + timedelta(days=30)).isoformat()
            submit_adhoc_line_qc(
                sid,
                body={
                    'answers': {
                        'use_by': {'value': use_by},
                        'product_temperature': {'value': '4'},
                        'spec_check': {'value': True},
                    },
                },
            )
        else:
            submit_adhoc_header_qc(
                sid,
                body={
                    'checked_by_user_id': 7,
                    'delivery_date': date.today().isoformat(),
                    'answers': {
                        'damaged_product': {'value': False},
                        'reject_delivery': {'value': False},
                    },
                },
            )
            submit_adhoc_line_qc(
                sid,
                body={'answers': {'spec_check': {'value': True}}},
            )
        return sid

    def test_start_header_line_qc_happy_path(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
            created_by_user_id=7,
        )
        self.assertEqual(form['status'], AdhocGoodsInStatus.OPEN)
        self.assertEqual(form['product_id'], self.product.id)
        self.assertTrue(form['header']['items'])
        self.assertTrue(form['line']['template']['items'])

        delivery = date.today()
        header = submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 7,
                'delivery_date': delivery.isoformat(),
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {'value': False},
                },
            },
        )
        self.assertEqual(header['status'], AdhocGoodsInStatus.OPEN)
        self.assertEqual(
            header['delivery_trace_number'],
            julian_trace_number(delivery),
        )
        self.assertIsNotNone(header['checked_at'])

        line = submit_adhoc_line_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 7,
                'answers': {'spec_check': {'value': True}},
            },
        )
        self.assertEqual(line['status'], AdhocGoodsInStatus.QC_COMPLETE)
        self.assertTrue(line['line']['line_check_ok'])

    def test_line_qc_requires_header(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
        )
        with self.assertRaises(AdhocGoodsInError) as ctx:
            submit_adhoc_line_qc(
                form['session_id'],
                body={'answers': {'spec_check': {'value': True}}},
            )
        self.assertIn('header QC', str(ctx.exception))

    def test_reject_blocks_line_qc(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
        )
        submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 1,
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {'value': True, 'comment': 'bad'},
                },
            },
        )
        with self.assertRaises(AdhocGoodsInError) as ctx:
            submit_adhoc_line_qc(
                form['session_id'],
                body={'answers': {'spec_check': {'value': True}}},
            )
        self.assertIn('rejected', str(ctx.exception).lower())

    def test_food_header_and_line_templates(self):
        form = start_adhoc_goods_in(
            product_id=self.food.id,
            location_id=self.wh.id,
        )
        codes = {i['code'] for i in form['header']['items']}
        self.assertIn('vehicle_temperature', codes)
        self.assertIn('vehicle_clean_fb_pest_odour', codes)
        line_codes = {i['code'] for i in form['line']['template']['items']}
        self.assertIn('use_by', line_codes)
        self.assertIn('product_temperature', line_codes)

        use_by = (date.today() + timedelta(days=30)).isoformat()
        submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 1,
                'delivery_date': date.today().isoformat(),
                'answers': {
                    'vehicle_clean_fb_pest_odour': {'value': True},
                    'primary_outer_packaging_damaged': {'value': False},
                    'reject_delivery': {'value': False},
                },
            },
        )
        line = submit_adhoc_line_qc(
            form['session_id'],
            body={
                'answers': {
                    'use_by': {'value': use_by},
                    'product_temperature': {'value': '4'},
                    'spec_check': {'value': True},
                },
            },
        )
        self.assertEqual(line['status'], AdhocGoodsInStatus.QC_COMPLETE)
        self.assertEqual(line['line']['use_by'], use_by)

    def test_line_qc_returns_failed_details(self):
        from product.models import (
            ProductAcceptance,
            ProductStorageRegime,
            ProductTechnical,
        )

        ProductTechnical.objects.create(
            product=self.food,
            storage_regime=ProductStorageRegime.FROZEN,
            temp_check_lower_bound=Decimal('-25'),
            temp_check_upper_bound=Decimal('-18'),
        )
        ProductAcceptance.objects.create(
            product=self.food,
            min_acceptable_shelf_life_days=14,
        )
        form = start_adhoc_goods_in(
            product_id=self.food.id,
            location_id=self.wh.id,
        )
        delivery = date.today()
        submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 1,
                'delivery_date': delivery.isoformat(),
                'answers': {
                    'vehicle_clean_fb_pest_odour': {'value': True},
                    'primary_outer_packaging_damaged': {'value': False},
                    'vehicle_temperature': {'value': '-18'},
                    'reject_delivery': {'value': False},
                },
            },
        )
        use_by = (delivery + timedelta(days=2)).isoformat()
        result = submit_adhoc_line_qc(
            form['session_id'],
            body={
                'answers': {
                    'use_by': {'value': use_by},
                    'product_temperature': {'value': '2.5'},
                    'spec_check': {'value': True},
                },
            },
        )
        self.assertFalse(result['line_check_ok'])
        self.assertFalse(result['line']['line_check_ok'])
        self.assertEqual(result['status'], AdhocGoodsInStatus.OPEN)
        self.assertIn('use_by', result['failed_codes'])
        self.assertIn('product_temperature', result['failed_codes'])
        self.assertTrue(result['failed_details'])
        by_code = {d['code']: d for d in result['failed_details']}
        self.assertEqual(by_code['use_by']['reason'], 'shelf_life')
        self.assertIn('14 days', by_code['use_by']['message'])
        self.assertEqual(by_code['product_temperature']['reason'], 'out_of_range')
        self.assertIn('-25', by_code['product_temperature']['message'])
        self.assertIn('-18', by_code['product_temperature']['message'])
        self.assertIn('Inform QC/QA', result['qc_blocked_message'])

    def test_http_start_and_get(self):
        client = Client()
        resp = client.post(
            '/purchasing/adhoc-goods-in/',
            data={
                'product_id': self.product.id,
                'location_id': self.wh.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['status'], 'success')
        session_id = body['data']['session_id']

        get_resp = client.get(f'/purchasing/adhoc-goods-in/{session_id}/')
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()['data']['session_id'], session_id)

    def test_receive_requires_qc_complete(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
        )
        with self.assertRaises(AdhocGoodsInError) as ctx:
            receive_adhoc_goods_in(
                form['session_id'],
                body={
                    'idempotency_key': f'adhoc-{uuid4()}',
                    'quantity': '1',
                    'product_supplier_id': self.mapping.id,
                    'label_format': 'pallet',
                    'label_count': 1,
                },
            )
        self.assertIn('line QC', str(ctx.exception).lower())

    def test_receive_pack_shape_queued(self):
        sid = self._qc_complete()
        key = f'adhoc-recv-{uuid4()}'
        result = receive_adhoc_goods_in(
            sid,
            body={
                'idempotency_key': key,
                'item_po_ref': 'SUP-PO-99',
                'product_supplier_id': self.mapping.id,
                'quantity': '2',
                'label_format': 'pallet',
                'label_count': 1,
            },
        )
        self.assertEqual(result['status'], AdhocGoodsInStatus.RECEIVED)
        self.assertEqual(result['item_po_ref'], 'SUP-PO-99')
        self.assertEqual(result['stock_qty'], '10.000000')
        self.assertEqual(len(result['receive_results']), 1)
        entry_id = result['receive_results'][0]['stock_entry_id']
        entry = StockEntry.objects.get(pk=entry_id)
        self.assertEqual(entry.source_document_type, 'adhoc_goods_in')
        self.assertEqual(entry.source_document_id, sid)
        self.assertIsNone(entry.po_number)
        self.assertIn('SUP-PO-99', entry.remarks or '')
        self.assertEqual(result['receive_results'][0]['posting_status'], 'queued')

    def test_receive_free_qty(self):
        sid = self._qc_complete()
        result = receive_adhoc_goods_in(
            sid,
            body={
                'idempotency_key': f'adhoc-free-{uuid4()}',
                'quantity': '5',
                'label_format': 'box',
                'label_count': 1,
                'supplier_id': self.supplier.id,
            },
        )
        self.assertEqual(result['status'], AdhocGoodsInStatus.RECEIVED)
        self.assertEqual(result['stock_qty'], '5.000000')
        entry = StockEntry.objects.get(
            pk=result['receive_results'][0]['stock_entry_id'],
        )
        self.assertEqual(entry.quantity, Decimal('5'))
