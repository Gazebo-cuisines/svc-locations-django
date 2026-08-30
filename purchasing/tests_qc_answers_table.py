"""Goods-in answers table dual-write on existing POST QC."""

from datetime import date
from uuid import uuid4

from django.test import TestCase

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
from purchasing.models import GoodsInAnswer
from purchasing.services.adhoc_goods_in import (
    get_adhoc_goods_in,
    start_adhoc_goods_in,
    submit_adhoc_header_qc,
    submit_adhoc_line_qc,
)


class QcAnswersTableTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=51, name='Ans Class')
        Category.objects.create(id=51, name='Ans Cat')
        Range.objects.create(id=51, name='Ans Range')
        self.kg = Unit.objects.create(id=51, name='Kg')
        self.wh = Location.objects.create(id=51, name='Ans WH', visible=True)
        self.product = Product.objects.create(
            name=f'Box {uuid4().hex[:8]}',
            recipe_code=f'BX{uuid4().hex[:6]}',
            product_class_id=51,
            category_id=51,
            range_id=51,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )

    def test_header_and_line_rows_and_json_still_present(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
        )
        header = submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 9,
                'delivery_date': date.today().isoformat(),
                'answers': {
                    'damaged_product': {'value': False, 'comment': 'ok'},
                    'reject_delivery': {'value': False},
                },
            },
        )
        self.assertIn('damaged_product', header['saved_header_answers'])
        rows = list(GoodsInAnswer.objects.filter(
            adhoc_session_id=form['session_id'],
            scope='header',
        ))
        self.assertEqual(len(rows), 2)
        damaged = next(r for r in rows if r.check_code == 'damaged_product')
        self.assertIs(damaged.value_bool, False)
        self.assertEqual(damaged.comment, 'ok')
        self.assertEqual(damaged.answered_by_user_id, 9)

        line = submit_adhoc_line_qc(
            form['session_id'],
            body={'answers': {'spec_check': {'value': True}}},
        )
        self.assertTrue(line['line']['saved_answers']['spec_check']['value'])
        spec = GoodsInAnswer.objects.get(
            adhoc_line_id=line['line']['line_id'],
            check_code='spec_check',
        )
        self.assertIs(spec.value_bool, True)

        GoodsInAnswer.objects.filter(
            adhoc_session_id=form['session_id'],
            check_code='damaged_product',
        ).update(value_bool=True, comment='from table')
        again = get_adhoc_goods_in(form['session_id'])
        self.assertTrue(again['saved_header_answers']['damaged_product']['value'])
        self.assertEqual(
            again['saved_header_answers']['damaged_product']['comment'],
            'from table',
        )
