"""Activity feed quantity display (pack shape + kg)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductLabelMode,
    ProductSupplier,
    Range,
    Unit,
)
from stock_ledger.models import StockLot, StockLotOrigin
from stock_ledger.util import entry_posting, services
from stock_ledger.util.conversions import seed_global_unit_conversions
from users_rbac.models import Department, RbacUser, UserDepartment


class ActivityQuantityDisplayTests(TestCase):
    def setUp(self):
        seed_global_unit_conversions()
        ProductClass.objects.create(id=97, name='Act Class')
        Category.objects.create(id=97, name='Act Cat')
        Range.objects.create(id=97, name='Act Range')
        self.kg = Unit.objects.create(id=971, name='Kg')
        self.box = Unit.objects.create(id=972, name='Box')
        self.wh = Location.objects.create(id=971, name='Act WH', visible=True)
        self.supplier = Location.objects.create(id=972, name='Act Sup', visible=True)
        self.product = Product.objects.create(
            name=f'Act Peas {uuid4().hex[:6]}',
            recipe_code=f'AP{uuid4().hex[:4]}',
            product_class_id=97,
            category_id=97,
            range_id=97,
            unit=self.kg,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='ACT-10',
            supplier_product_name='PEAS 10KG',
            outer_qty=Decimal('1'),
            outer_unit=self.box,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-act-floor',
            username='actfloor',
        )
        UserDepartment.objects.create(user=self.floor, department=Department.WAREHOUSE)
        self.entry = services.receipt(
            idempotency_key=f'act-qty-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            unit_id=self.kg.id,
            counterparty_location_id=self.supplier.id,
            effective_at=timezone.now(),
            actor_user_id=self.floor.id,
            lan_username='actfloor',
        )
        self.client = Client()

    def test_me_activity_shows_pack_shape_not_grams_only(self):
        with patch('users_rbac.auth.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = self.floor
                return None

            mock_attach.side_effect = _set_user
            resp = self.client.get('/auth/me/activity/')

        self.assertEqual(resp.status_code, 200, resp.content)
        row = next(
            item for item in resp.json()['data']['items']
            if item['entry_id'] == self.entry.id
        )
        self.assertEqual(row['pack_quantity'], '2')
        self.assertEqual(row['pack_unit_name'], 'Box')
        self.assertEqual(row['shape_format_label'], '1BOX x 10KG = 10KG')
        self.assertEqual(row['display_kg'], '20')
        self.assertIn('2 Box', row['quantity_display'])
        self.assertIn('1BOX x 10KG = 10KG', row['quantity_display'])
        self.assertEqual(row['use_by'], '2026-12-01')

    def test_me_activity_paginates_at_database(self):
        for idx in range(3):
            services.receipt(
                idempotency_key=f'act-page-{uuid4()}',
                lot=self.lot,
                location_id=self.wh.id,
                quantity=Decimal('1'),
                unit_id=self.kg.id,
                counterparty_location_id=self.supplier.id,
                effective_at=timezone.now(),
                actor_user_id=self.floor.id,
                lan_username='actfloor',
            )
        with patch('users_rbac.auth.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = self.floor
                return None

            mock_attach.side_effect = _set_user
            page1 = self.client.get('/auth/me/activity/?limit=2&offset=0')
            page2 = self.client.get('/auth/me/activity/?limit=2&offset=2')

        self.assertEqual(page1.status_code, 200, page1.content)
        self.assertEqual(page2.status_code, 200, page2.content)
        d1 = page1.json()['data']
        d2 = page2.json()['data']
        self.assertEqual(len(d1['items']), 2)
        self.assertGreaterEqual(d1['count'], 4)
        self.assertTrue(d1['has_more'])
        self.assertEqual(len(d2['items']), 2)
        ids1 = {row['entry_id'] for row in d1['items']}
        ids2 = {row['entry_id'] for row in d2['items']}
        self.assertFalse(ids1 & ids2)

    def test_me_activity_shows_queued_status_and_reprint_flag(self):
        entry = services.receipt(
            idempotency_key=f'act-q-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.kg.id,
            counterparty_location_id=self.supplier.id,
            effective_at=timezone.now(),
            defer_balance=True,
            actor_user_id=self.floor.id,
            lan_username='actfloor',
        )
        entry_posting.queue_entry(entry=entry)
        from stock_ledger.util import entry_labels
        entry_labels.create_entry_label(
            entry=entry,
            label_format='box',
            label_count=1,
        )
        with patch('users_rbac.auth.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = self.floor
                return None

            mock_attach.side_effect = _set_user
            resp = self.client.get('/auth/me/activity/?limit=10&offset=0')

        row = next(
            item for item in resp.json()['data']['items']
            if item['entry_id'] == entry.id
        )
        self.assertEqual(row['ui_status'], 'queued')
        self.assertEqual(row['posting_status'], 'queued')
        self.assertEqual(row['label_status'], 'pending')
        self.assertTrue(row['can_reprint_label'])
        self.assertEqual(
            row['label_reprint_path'],
            f'/stock/entries/{entry.id}/labels/print/',
        )

    def test_me_activity_flags_user_cancelled_queued_receipt(self):
        entry = services.receipt(
            idempotency_key=f'act-cancel-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.kg.id,
            counterparty_location_id=self.supplier.id,
            effective_at=timezone.now(),
            defer_balance=True,
            actor_user_id=self.floor.id,
            lan_username='actfloor',
        )
        entry_posting.queue_entry(
            entry=entry,
            actor_user_id=self.floor.id,
            lan_username='actfloor',
        )
        entry_posting.cancel_entry(entry_id=entry.id)

        with patch('users_rbac.auth.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = self.floor
                return None

            mock_attach.side_effect = _set_user
            resp = self.client.get('/auth/me/activity/?limit=10&offset=0')

        row = next(
            item for item in resp.json()['data']['items']
            if item['entry_id'] == entry.id
        )
        self.assertFalse(row['is_live'])
        self.assertTrue(row['is_removed'])
        self.assertTrue(row['user_cancelled'])
        self.assertFalse(row['manager_removed'])
        self.assertEqual(row['ui_status'], 'cancelled')
        self.assertEqual(row['posting_status'], 'cancelled')
        self.assertIsNotNone(row['removed_at'])
        self.assertEqual(row['removed_by_user_id'], self.floor.id)
        self.assertEqual(row['removed_by_name'], 'actfloor')
        self.assertFalse(row['can_reprint_label'])
