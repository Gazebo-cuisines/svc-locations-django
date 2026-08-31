"""Soft lock: second user gets 409 until TTL expires."""

from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

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
from purchasing.models import AdhocGoodsInSession
from purchasing.services.adhoc_goods_in import start_adhoc_goods_in
from purchasing.services.draft_qc import draft_adhoc_header_qc
from purchasing.services.qc_lock import QcLockError
from users_rbac.models import RbacUser


class QcLockTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=31, name='Lk Class')
        Category.objects.create(id=31, name='Lk Cat')
        Range.objects.create(id=31, name='Lk Range')
        kg = Unit.objects.create(id=31, name='Kg')
        self.wh = Location.objects.create(id=31, name='Lk WH', visible=True)
        self.product = Product.objects.create(
            name=f'Oil {uuid4().hex[:8]}',
            recipe_code=f'OL{uuid4().hex[:6]}',
            product_class_id=31,
            category_id=31,
            range_id=31,
            unit=kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.amit = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )
        self.jane = RbacUser.objects.create(
            cognito_sub='sub-jane',
            username='jane01',
            display_name='Jane',
        )

    def test_second_user_blocked_then_takes_after_ttl(self):
        form = start_adhoc_goods_in(
            product_id=self.product.id,
            location_id=self.wh.id,
        )
        first = draft_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': self.amit.id,
                'answers': {'damaged_product': {'value': False}},
            },
        )
        self.assertEqual(first['lock']['editor_user_id'], self.amit.id)
        self.assertEqual(first['lock']['editor_name'], 'Amit')
        with self.assertRaises(QcLockError) as ctx:
            draft_adhoc_header_qc(
                form['session_id'],
                body={
                    'checked_by_user_id': self.jane.id,
                    'answers': {'reject_delivery': {'value': False}},
                },
            )
        self.assertIn('Amit', str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 409)

        session = AdhocGoodsInSession.objects.get(pk=form['session_id'])
        session.editor_heartbeat_at = timezone.now() - timedelta(minutes=3)
        session.save(update_fields=['editor_heartbeat_at'])
        second = draft_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': self.jane.id,
                'answers': {'reject_delivery': {'value': False}},
            },
        )
        self.assertEqual(second['lock']['editor_user_id'], self.jane.id)
        draft_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': self.jane.id,
                'answers': {'comment': {'value': 'same user'}},
            },
        )
