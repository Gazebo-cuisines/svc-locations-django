"""Stock Management Tool — remove queued, simple + cascade posted entries."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from planning.models import Resource
from product.models import Category, Product, ProductClass, ProductLabelMode, Range, Unit
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockUnitStatus,
)
from stock_ledger.util import entry_posting, stock_units
from stock_ledger.util import services
from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)


class StockManagementRemoveTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=93, name='RM Class')
        Category.objects.create(id=93, name='RM Cat')
        Range.objects.create(id=93, name='RM Range')
        self.unit = Unit.objects.create(id=93, name='Kg')
        self.wh = Location.objects.create(id=93, name='RM WH', visible=True)
        self.dest = Location.objects.create(id=94, name='RM Dest', visible=True)
        self.product = Product.objects.create(
            name=f'RM Peas {uuid4().hex[:8]}',
            recipe_code=f'RM{uuid4().hex[:6]}',
            product_class_id=93,
            category_id=93,
            range_id=93,
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
        self.resource = Resource.objects.create(
            id=995,
            code='RM-RES',
            name='RM Resource',
            location=self.wh,
            is_active=True,
        )
        self.fg_product = Product.objects.create(
            name=f'RM FG {uuid4().hex[:8]}',
            recipe_code=f'FG{uuid4().hex[:6]}',
            product_class_id=93,
            category_id=93,
            range_id=93,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.fg_lot = StockLot.objects.create(
            product=self.fg_product,
            trace_number=f'F{uuid4().hex[:8]}',
            origin=StockLotOrigin.PRODUCTION,
            use_by=date(2026, 12, 15),
            production_date=date(2026, 9, 1),
        )
        self.posted = services.receipt(
            idempotency_key=f'rm-posted-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        self.client = Client()
        self.manager = RbacUser.objects.create(
            cognito_sub='sub-mgr-rm',
            username='mgrremove',
        )
        UserDepartment.objects.create(user=self.manager, department=Department.ADMIN)
        AdminAccess.objects.create(
            user=self.manager,
            area=AdminArea.STOCK_MANAGEMENT,
        )
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-wh-rm',
            username='whremove',
        )
        UserDepartment.objects.create(user=self.floor, department=Department.WAREHOUSE)
        WarehouseAccess.objects.create(
            user=self.floor,
            unit=WarehouseUnit.UNIT_2,
            can_goods_in=True,
        )

    def _queued_receipt(self):
        entry = services.receipt(
            idempotency_key=f'rm-q-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('40'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            defer_balance=True,
        )
        entry_posting.queue_entry(entry=entry)
        return entry

    def _post_remove(self, entry_id, *, user, body=None):
        payload = body or {
            'reason': 'Wrong qty on pallet',
            'idempotency_key': f'rm-remove-{uuid4()}',
        }
        with patch('users_rbac.permissions.attach_user') as mock_attach:
            def _set_user(request, **kwargs):
                request.rbac_user = user
                return None

            mock_attach.side_effect = _set_user
            return self.client.post(
                f'/stock/manage/entries/{entry_id}/remove/',
                data=payload,
                content_type='application/json',
            )

    def test_cancel_queued_receipt(self):
        entry = self._queued_receipt()
        before = StockBalance.objects.filter(
            lot=self.lot,
            location_id=self.wh.id,
        ).first()
        before_qty = before.quantity if before else Decimal('0')

        resp = self._post_remove(entry.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['cancelled_entry_codes'], [f'E{entry.id}'])
        self.assertEqual(data['preview']['action'], 'already_removed')

        posting = StockEntryPosting.objects.get(stock_entry_id=entry.id)
        self.assertEqual(posting.status, StockEntryPostingStatus.CANCELLED)
        self.assertEqual(
            posting.meta['manager_remove']['reason'],
            'Wrong qty on pallet',
        )

        after = StockBalance.objects.filter(
            lot=self.lot,
            location_id=self.wh.id,
        ).first()
        after_qty = after.quantity if after else Decimal('0')
        self.assertEqual(after_qty, before_qty)

    def test_remove_is_idempotent(self):
        entry = self._queued_receipt()
        key = f'rm-idem-{uuid4()}'
        body = {'reason': 'Duplicate test', 'idempotency_key': key}
        first = self._post_remove(entry.id, user=self.manager, body=body)
        second = self._post_remove(entry.id, user=self.manager, body=body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.json()['data']['idempotent'])

    def test_posted_receipt_reverse_restores_balance(self):
        resp = self._post_remove(self.posted.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['reversed_entry_codes'], [f'E{self.posted.id}'])
        self.assertEqual(data['preview']['action'], 'already_removed')

        bal = StockBalance.objects.filter(
            lot=self.lot,
            location_id=self.wh.id,
        ).first()
        self.assertTrue(bal is None or bal.quantity == 0)

        self.posted.refresh_from_db()
        self.assertEqual(self.posted.reversed_by.entry_type, StockEntryType.REVERSAL)

    def test_posted_issue_reverse_restores_balance(self):
        issue = services.issue(
            idempotency_key=f'rm-issue-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('30'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )
        bal_before = StockBalance.objects.get(lot=self.lot, location_id=self.wh.id)
        self.assertEqual(bal_before.quantity, Decimal('70'))

        resp = self._post_remove(issue.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(
            resp.json()['data']['reversed_entry_codes'],
            [f'E{issue.id}'],
        )
        bal_before.refresh_from_db()
        self.assertEqual(bal_before.quantity, Decimal('100'))

    def test_posted_receipt_with_pick_cascade_reverse(self):
        issue = services.issue(
            idempotency_key=f'rm-pick-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            source_entry=self.posted,
            effective_at=timezone.now(),
        )
        resp = self._post_remove(self.posted.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        codes = resp.json()['data']['reversed_entry_codes']
        self.assertEqual(codes, [f'E{issue.id}', f'E{self.posted.id}'])

        bal = StockBalance.objects.filter(
            lot=self.lot,
            location_id=self.wh.id,
        ).first()
        self.assertTrue(bal is None or bal.quantity == 0)
        self.posted.refresh_from_db()
        issue.refresh_from_db()
        self.assertEqual(self.posted.reversed_by.entry_type, StockEntryType.REVERSAL)
        self.assertEqual(issue.reversed_by.entry_type, StockEntryType.REVERSAL)

    def test_transfer_both_legs_reversed(self):
        out_entry, in_entry = services.transfer(
            idempotency_key=f'rm-xfer-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.dest.id,
            quantity=Decimal('25'),
            unit_id=self.unit.id,
        )
        wh_bal = StockBalance.objects.get(lot=self.lot, location_id=self.wh.id)
        dest_bal = StockBalance.objects.get(lot=self.lot, location_id=self.dest.id)
        self.assertEqual(wh_bal.quantity, Decimal('75'))
        self.assertEqual(dest_bal.quantity, Decimal('25'))

        resp = self._post_remove(out_entry.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        codes = resp.json()['data']['reversed_entry_codes']
        self.assertEqual(codes, [f'E{out_entry.id}', f'E{in_entry.id}'])

        wh_bal.refresh_from_db()
        self.assertEqual(wh_bal.quantity, Decimal('100'))
        dest_bal = StockBalance.objects.filter(
            lot=self.lot,
            location_id=self.dest.id,
        ).first()
        self.assertTrue(dest_bal is None or dest_bal.quantity == 0)

    def test_production_output_void(self):
        output, _run = services.production_output(
            idempotency_key=f'rm-made-{uuid4()}',
            lot=self.fg_lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            resource_id=self.resource.id,
            base_date=date(2026, 9, 1),
        )
        cons = services.production_consume(
            idempotency_key=f'rm-cons-{uuid4()}',
            output_entry_id=output.id,
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
        )
        rm_before = StockBalance.objects.get(lot=self.lot, location_id=self.wh.id)
        self.assertEqual(rm_before.quantity, Decimal('95'))

        resp = self._post_remove(output.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        codes = resp.json()['data']['reversed_entry_codes']
        self.assertEqual(codes, [f'E{cons.id}', f'E{output.id}'])

        rm_before.refresh_from_db()
        self.assertEqual(rm_before.quantity, Decimal('100'))
        fg_bal = StockBalance.objects.filter(
            lot=self.fg_lot,
            location_id=self.wh.id,
        ).first()
        self.assertTrue(fg_bal is None or fg_bal.quantity == 0)

    def test_production_consumption_direct_still_blocked(self):
        output, _run = services.production_output(
            idempotency_key=f'rm-made2-{uuid4()}',
            lot=self.fg_lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            resource_id=self.resource.id,
            base_date=date(2026, 9, 1),
        )
        cons = services.production_consume(
            idempotency_key=f'rm-cons2-{uuid4()}',
            output_entry_id=output.id,
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
        )
        resp = self._post_remove(cons.id, user=self.manager)
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertIn('production', resp.json()['message'].lower())

    def test_receipt_blocked_until_production_voided(self):
        output, _run = services.production_output(
            idempotency_key=f'rm-made3-{uuid4()}',
            lot=self.fg_lot,
            location_id=self.wh.id,
            quantity=Decimal('20'),
            resource_id=self.resource.id,
            base_date=date(2026, 9, 1),
        )
        services.production_consume(
            idempotency_key=f'rm-cons3-{uuid4()}',
            output_entry_id=output.id,
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            source_entry=self.posted,
        )
        blocked = self._post_remove(self.posted.id, user=self.manager)
        self.assertEqual(blocked.status_code, 409, blocked.content)

        void_prod = self._post_remove(output.id, user=self.manager)
        self.assertEqual(void_prod.status_code, 201, void_prod.content)

        unblocked = self._post_remove(self.posted.id, user=self.manager)
        self.assertEqual(unblocked.status_code, 201, unblocked.content)

    def test_reverse_is_idempotent(self):
        key = f'rm-rev-idem-{uuid4()}'
        body = {'reason': 'Wrong batch', 'idempotency_key': key}
        first = self._post_remove(self.posted.id, user=self.manager, body=body)
        second = self._post_remove(self.posted.id, user=self.manager, body=body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.json()['data']['idempotent'])

    def test_floor_user_denied(self):
        entry = self._queued_receipt()
        resp = self._post_remove(entry.id, user=self.floor)
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_voids_units_on_cancel(self):
        entry = self._queued_receipt()
        created = stock_units.create_units_for_entry(
            source_entry=entry,
            unit_count=2,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'rm-u-{uuid4()}',
        )
        resp = self._post_remove(entry.id, user=self.manager)
        self.assertEqual(resp.status_code, 201, resp.content)
        serials = resp.json()['data']['voided_unit_serials']
        self.assertEqual(len(serials), 2)
        for unit in created:
            unit.refresh_from_db()
            self.assertEqual(unit.status, StockUnitStatus.VOID)

    def test_reason_required(self):
        entry = self._queued_receipt()
        resp = self._post_remove(
            entry.id,
            user=self.manager,
            body={'reason': '   ', 'idempotency_key': f'rm-{uuid4()}'},
        )
        self.assertEqual(resp.status_code, 400, resp.content)
