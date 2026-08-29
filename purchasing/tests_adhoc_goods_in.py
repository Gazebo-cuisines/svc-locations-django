"""Without-PO goods-in QC session (Chunk 2)."""

from datetime import date, timedelta
from uuid import uuid4

from django.test import Client, TestCase

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    ProductLabelMode,
    Range,
    Unit,
)
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import AdhocGoodsInStatus
from purchasing.services.adhoc_goods_in import (
    AdhocGoodsInError,
    start_adhoc_goods_in,
    submit_adhoc_header_qc,
    submit_adhoc_line_qc,
)
from purchasing.services.julian import julian_trace_number


class AdhocGoodsInQcTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=81, name='Adhoc Class')
        Category.objects.create(id=81, name='Adhoc Cat')
        Range.objects.create(id=81, name='Adhoc Range')
        self.kg = Unit.objects.create(id=81, name='Kg')
        self.wh = Location.objects.create(id=81, name='Adhoc WH', visible=True)
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
