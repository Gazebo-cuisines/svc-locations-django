"""Stock Management Tool — hide removed entries from scan + goods-in/out reports."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import Category, Product, ProductClass, ProductLabelMode, Range, Unit
from stock_ledger.models import StockLot, StockLotOrigin, StockUnitStatus
from stock_ledger.util import entry_posting, stock_units
from stock_ledger.util import services
from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
)


class StockManagementHideOpsTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=96, name='Hide Class')
        Category.objects.create(id=96, name='Hide Cat')
        Range.objects.create(id=96, name='Hide Range')
        self.unit = Unit.objects.create(id=96, name='Kg')
        self.wh = Location.objects.create(id=96, name='Hide WH', visible=True)
        self.product = Product.objects.create(
            name=f'Hide Peas {uuid4().hex[:8]}',
            recipe_code=f'HP{uuid4().hex[:6]}',
            product_class_id=96,
            category_id=96,
            range_id=96,
            unit=self.unit,
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        self.today = timezone.localdate()
        self.posted = services.receipt(
            idempotency_key=f'hide-posted-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()
        self.manager = RbacUser.objects.create(
            cognito_sub='sub-mgr-hide',
            username='mgrhide',
        )
        UserDepartment.objects.create(user=self.manager, department=Department.ADMIN)
        AdminAccess.objects.create(
            user=self.manager,
            area=AdminArea.STOCK_MANAGEMENT,
        )

    def _queued_receipt(self):
        entry = services.receipt(
            idempotency_key=f'hide-q-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('40'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            defer_balance=True,
        )
        entry_posting.queue_entry(entry=entry)
        return entry

    def _post_remove(self, entry_id):
        with patch('users_rbac.permissions.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = self.manager
                return None

            mock_attach.side_effect = _set_user
            return self.client.post(
                f'/stock/manage/entries/{entry_id}/remove/',
                data={
                    'reason': 'Wrong label',
                    'idempotency_key': f'hide-rm-{uuid4()}',
                },
                content_type='application/json',
            )

    def _goods_in_ids(self):
        resp = self.client.get(
            '/stock/reports/goods-in/',
            {
                'date_from': self.today.isoformat(),
                'date_to': self.today.isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row['entry_id'] for row in resp.json()['data']['results']}

    def test_cancelled_queued_receipt_scan_fails_and_goods_in_omits(self):
        entry = self._queued_receipt()
        resp = self._post_remove(entry.id)
        self.assertEqual(resp.status_code, 201, resp.content)

        scan = self.client.get(f'/stock/scan/?code=E{entry.id}')
        self.assertEqual(scan.status_code, 400, scan.content)
        self.assertIn('void', scan.json()['message'].lower())

        self.assertNotIn(entry.id, self._goods_in_ids())

    def test_reversed_posted_receipt_scan_fails_and_goods_in_omits(self):
        resp = self._post_remove(self.posted.id)
        self.assertEqual(resp.status_code, 201, resp.content)

        scan = self.client.get(f'/stock/scan/?code=E{self.posted.id}')
        self.assertEqual(scan.status_code, 400, scan.content)
        self.assertIn('void', scan.json()['message'].lower())

        self.assertNotIn(self.posted.id, self._goods_in_ids())

    def test_reversed_issue_omits_from_goods_out(self):
        issue = services.issue(
            idempotency_key=f'hide-issue-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        resp = self._post_remove(issue.id)
        self.assertEqual(resp.status_code, 201, resp.content)

        report = self.client.get(
            '/stock/reports/goods-out/',
            {
                'date_from': self.today.isoformat(),
                'date_to': self.today.isoformat(),
            },
        )
        self.assertEqual(report.status_code, 200, report.content)
        ids = {row['entry_id'] for row in report.json()['data']['results']}
        self.assertNotIn(issue.id, ids)

    def test_void_unit_serial_scan_fails(self):
        units = stock_units.create_units_for_entry(
            source_entry=self.posted,
            unit_count=1,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'hide-u-{uuid4()}',
        )
        unit = units[0]
        stock_units.void_unit(unit_serial=unit.unit_serial, reason='misprint')

        scan = self.client.get(f'/stock/scan/?code={unit.unit_serial}')
        self.assertEqual(scan.status_code, 400, scan.content)
        self.assertIn('void', scan.json()['message'].lower())
        unit.refresh_from_db()
        self.assertEqual(unit.status, StockUnitStatus.VOID)

    def test_audit_timeline_still_shows_reversed_receipt(self):
        resp = self._post_remove(self.posted.id)
        self.assertEqual(resp.status_code, 201, resp.content)

        timeline = self.client.get(
            f'/stock/audit/timeline/?product_id={self.product.id}',
        )
        self.assertEqual(timeline.status_code, 200, timeline.content)
        entry_ids = {row['entry_id'] for row in timeline.json()['data']}
        self.assertIn(self.posted.id, entry_ids)
