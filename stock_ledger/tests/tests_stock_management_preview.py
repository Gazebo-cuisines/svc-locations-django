"""Stock Management Tool — preview API (chunk 2)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from planning.models import Resource
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import (
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
    StockLot,
    StockLotOrigin,
)
from stock_ledger.util import entry_posting, services
from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)


class StockManagementPreviewTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=91, name='MG Class')
        Category.objects.create(id=91, name='MG Cat')
        Range.objects.create(id=91, name='MG Range')
        self.unit = Unit.objects.create(id=91, name='Kg')
        self.wh = Location.objects.create(id=91, name='MG WH', visible=True)
        self.dest = Location.objects.create(id=92, name='MG Dest', visible=True)
        self.product = Product.objects.create(
            name=f'MG Peas {uuid4().hex[:8]}',
            recipe_code=f'MG{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.dest,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        self.resource = Resource.objects.create(
            id=991,
            code='MG-RES',
            name='MG Resource',
            location=self.wh,
            is_active=True,
        )
        self.posted = services.receipt(
            idempotency_key=f'mg-in-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()
        self.manager = RbacUser.objects.create(
            cognito_sub='sub-mgr-preview',
            username='mgrpreview',
        )
        UserDepartment.objects.create(user=self.manager, department=Department.ADMIN)
        AdminAccess.objects.create(
            user=self.manager,
            area=AdminArea.STOCK_MANAGEMENT,
        )
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-wh-preview',
            username='whpreview',
        )
        UserDepartment.objects.create(user=self.floor, department=Department.WAREHOUSE)
        WarehouseAccess.objects.create(
            user=self.floor,
            unit=WarehouseUnit.UNIT_2,
            can_goods_in=True,
        )

    def _get_preview(self, entry_id, *, user):
        with patch('users_rbac.permissions.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = user
                return None

            mock_attach.side_effect = _set_user
            return self.client.get(f'/stock/manage/entries/{entry_id}/')

    def test_queued_receipt_preview_cancel(self):
        queued = services.receipt(
            idempotency_key=f'mg-q-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            defer_balance=True,
        )
        entry_posting.queue_entry(entry=queued)
        resp = self._get_preview(queued.id, user=self.manager)
        self.assertEqual(resp.status_code, 200, resp.content)
        preview = resp.json()['data']['preview']
        self.assertEqual(preview['action'], 'cancel')
        self.assertEqual(len(preview['will_undo']), 1)
        self.assertEqual(preview['will_undo'][0]['step'], 'cancel')

    def test_posted_receipt_preview_reverse(self):
        resp = self._get_preview(self.posted.id, user=self.manager)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['entry_code'], f'E{self.posted.id}')
        preview = data['preview']
        self.assertEqual(preview['action'], 'reverse')
        self.assertEqual(
            preview['will_undo'][-1]['entry_id'],
            self.posted.id,
        )

    def test_receipt_with_issue_draw_lists_both(self):
        issue = services.issue(
            idempotency_key=f'mg-out-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            source_entry=self.posted,
        )
        resp = self._get_preview(self.posted.id, user=self.manager)
        preview = resp.json()['data']['preview']
        self.assertEqual(preview['action'], 'reverse')
        ids = [row['entry_id'] for row in preview['will_undo']]
        self.assertEqual(ids, [issue.id, self.posted.id])

    def test_transfer_preview_includes_both_legs(self):
        out_entry, in_entry = services.transfer(
            idempotency_key=f'mg-xfer-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
        )
        resp = self._get_preview(out_entry.id, user=self.manager)
        preview = resp.json()['data']['preview']
        self.assertEqual(preview['action'], 'reverse')
        ids = [row['entry_id'] for row in preview['will_undo']]
        self.assertIn(out_entry.id, ids)
        self.assertIn(in_entry.id, ids)

    def test_already_reversed_preview(self):
        services.reversal(
            idempotency_key=f'mg-rev-{uuid4()}',
            entry=self.posted,
        )
        resp = self._get_preview(self.posted.id, user=self.manager)
        preview = resp.json()['data']['preview']
        self.assertEqual(preview['action'], 'already_removed')
        self.assertEqual(preview['will_undo'], [])

    def test_production_consumption_entry_blocked(self):
        output, _run = services.production_output(
            idempotency_key=f'mg-made-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            resource_id=self.resource.id,
            base_date=date(2026, 9, 1),
        )
        cons = services.production_consume(
            idempotency_key=f'mg-cons-{uuid4()}',
            output_entry_id=output.id,
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
        )
        resp = self._get_preview(cons.id, user=self.manager)
        preview = resp.json()['data']['preview']
        self.assertEqual(preview['action'], 'blocked')
        self.assertIn(f'E{output.id}', preview['block_reason'])

    def test_floor_user_denied(self):
        resp = self._get_preview(self.posted.id, user=self.floor)
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_missing_entry_404(self):
        resp = self._get_preview(999999999, user=self.manager)
        self.assertEqual(resp.status_code, 404, resp.content)
