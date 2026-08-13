from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from planning.models import (
    Plan,
    PlanEvent,
    PlanRequirement,
    PlanRun,
    PlanRunStatus,
    Resource,
)
from product.models import Category, Product, ProductClass, ProductSupplier, Range, Unit
from stock_ledger.models import (
    ProductionRun,
    StockEntry,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockPeriod,
)

class PickingListApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        self.unit11 = Location.objects.create(id=2, name='Unit 11', visible=True)
        self.sleeving = Location.objects.create(id=5, name='Sleeving', visible=True)
        self.high_risk = Location.objects.create(id=4, name='High Risk', visible=True)
        self.spice = Location.objects.create(id=15, name='Spice Room', visible=True)

        self.box = self._product(
            'Box - GRAB AND GO',
            source_id=self.unit11.id,
            dest_id=self.sleeving.id,
            recipe_code='BOX1',
        )
        self.label = self._product(
            'G&G Veg Samosa Label',
            source_id=self.unit11.id,
            dest_id=self.sleeving.id,
            recipe_code='LBL1',
        )
        self.spice_mix = self._product(
            'Spice Mix',
            source_id=self.spice.id,
            dest_id=self.high_risk.id,
            recipe_code='SP1',
        )

        self.plan = Plan.objects.create(
            plan_date=date(2026, 8, 1),
            location=self.sleeving,
        )
        self.run = PlanRun.objects.create(
            plan=self.plan,
            run_number=1,
            status=PlanRunStatus.COMPLETE,
            driver_version='test-1.0',
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )

        self._req(self.box, Decimal('20'), Decimal('20'), self.unit11, self.sleeving)
        self._req(self.box, Decimal('30'), Decimal('30'), self.unit11, self.sleeving)
        self._req(self.label, Decimal('300'), Decimal('300'), self.unit11, self.sleeving)
        self._req(
            self.spice_mix, Decimal('5'), Decimal('5'), self.spice, self.high_risk,
        )
        closed = self._req(
            self.box, Decimal('99'), Decimal('99'), self.unit11, self.sleeving,
        )
        closed.closed = True
        closed.save(update_fields=['closed'])

    def _product(self, name, *, source_id, dest_id, recipe_code):
        return Product.objects.create(
            name=name,
            recipe_code=recipe_code,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=source_id,
            destination_container_id=dest_id,
        )

    def _req(self, product, net, gross, source, dest):
        return PlanRequirement.objects.create(
            run=self.run,
            level=1,
            batch_number=1,
            product=product,
            net_required=net,
            gross_required=gross,
            yield_factor=Decimal('1'),
            process_loss=Decimal('1'),
            source_location=source,
            destination_location=dest,
        )

    def test_picking_list_aggregates_and_excludes_closed(self):
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{self.run.id}/picking-list/',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertIsNone(data['from_location'])
        self.assertIsNone(data['to_location'])
        self.assertNotIn('run_id', data)
        self.assertNotIn('plan_id', data)

        by_product = {row['product']: row for row in data['lines']}
        self.assertEqual(len(by_product), 3)
        box_row = by_product['Box - GRAB AND GO']
        self.assertEqual(box_row['gross_quantity'], '50.000000')
        self.assertEqual(box_row['from_location'], 'Unit 11')
        self.assertEqual(box_row['to_location'], 'Sleeving')
        self.assertEqual(box_row['unit'], 'Each')
        self.assertEqual(box_row['product_id'], self.box.id)
        self.assertEqual(box_row['from_location_id'], self.unit11.id)
        self.assertEqual(box_row['to_location_id'], self.sleeving.id)
        self.assertEqual(box_row['category_id'], 1)
        self.assertEqual(box_row['category_name'], 'Meals')
        self.assertIsNone(box_row['category_path'])
        self.assertEqual(box_row['category_l2'], 'Meals')
        self.assertIsNone(box_row['pack_quantity'])
        self.assertIsNone(box_row['pack_unit_name'])
        self.assertIsNone(box_row['shape_format_label'])
        self.assertEqual(len(box_row['requirement_ids']), 2)

        dept_names = {d['from_location'] for d in data['by_department']}
        self.assertEqual(dept_names, {'Unit 11', 'Spice Room'})
        unit11 = next(
            d for d in data['by_department'] if d['from_location'] == 'Unit 11'
        )
        self.assertEqual(unit11['line_count'], 2)
        self.assertNotIn('from_location_id', unit11)

    def test_picking_list_from_location_filter(self):
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{self.run.id}/picking-list/'
            f'?from_location=Unit%2011',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['from_location'], 'Unit 11')
        self.assertIsNone(data['to_location'])
        self.assertEqual(len(data['lines']), 2)
        self.assertTrue(
            all(row['from_location'] == 'Unit 11' for row in data['lines']),
        )
        self.assertEqual(len(data['by_department']), 2)

    def test_picking_list_to_location_filter(self):
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{self.run.id}/picking-list/'
            f'?to_location=Sleeving',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['to_location'], 'Sleeving')
        self.assertEqual(len(data['lines']), 2)
        self.assertTrue(
            all(row['to_location'] == 'Sleeving' for row in data['lines']),
        )
        self.assertTrue(
            all(row['from_location_id'] == self.unit11.id for row in data['lines']),
        )

    def test_picking_list_category_l2_from_path(self):
        raw = Category.objects.create(
            id=10, name='Raw Materials', path='Raw Materials',
        )
        pastry = Category.objects.create(
            id=11,
            name='Pastry Sheets',
            parent=raw,
            path='Raw Materials > Pastry > Pastry Sheets',
        )
        self.box.category = pastry
        self.box.save(update_fields=['category_id'])
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{self.run.id}/picking-list/',
        )
        box_row = next(
            row for row in resp.json()['data']['lines']
            if row['product_id'] == self.box.id
        )
        self.assertEqual(box_row['category_id'], 11)
        self.assertEqual(box_row['category_name'], 'Pastry Sheets')
        self.assertEqual(box_row['category_path'], 'Raw Materials > Pastry > Pastry Sheets')
        self.assertEqual(box_row['category_l2'], 'Pastry')
        self.assertEqual(box_row['from_location'], 'Unit 11')
        dept_names = {
            d['from_location']
            for d in resp.json()['data']['by_department']
        }
        self.assertEqual(dept_names, {'Unit 11', 'Spice Room'})

    def test_picking_list_pack_quantity_from_supplier_multiplier(self):
        box_uom = Unit.objects.create(id=2, name='Box')
        supplier = Location.objects.create(id=80, name='Pack Supplier', visible=True)
        ProductSupplier.objects.create(
            product=self.box,
            supplier=supplier,
            supplier_code='BOX-1X10',
            supplier_product_name='Grab box 10',
            outer_qty=Decimal('1'),
            outer_unit=box_uom,
            inner_qty=Decimal('10'),
            inner_unit_id=1,
            is_default=True,
            is_active=True,
        )
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{self.run.id}/picking-list/',
        )
        box_row = next(
            row for row in resp.json()['data']['lines']
            if row['product_id'] == self.box.id
        )
        self.assertEqual(box_row['net_quantity'], '50.000000')
        self.assertEqual(box_row['pack_quantity'], '5.000000')
        self.assertEqual(box_row['pack_unit_name'], 'Box')
        self.assertIn('BOX', box_row['shape_format_label'].upper())
        label_row = next(
            row for row in resp.json()['data']['lines']
            if row['product_id'] == self.label.id
        )
        self.assertIsNone(label_row['pack_quantity'])


class PlanPublishApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        self.loc = Location.objects.create(id=5, name='Sleeving', visible=True)
        self.plan = Plan.objects.create(
            plan_date=date(2026, 8, 2),
            location=self.loc,
        )

    def test_publish_requires_complete_run(self):
        resp = self.client.post(f'/planning/plans/{self.plan.id}/publish/')
        self.assertEqual(resp.status_code, 409)
        self.assertIsNone(Plan.objects.get(pk=self.plan.id).published_at)

    def test_publish_sets_published_at_and_event(self):
        PlanRun.objects.create(
            plan=self.plan,
            run_number=1,
            status=PlanRunStatus.COMPLETE,
            driver_version='test-1.0',
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        resp = self.client.post(f'/planning/plans/{self.plan.id}/publish/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertIsNotNone(data['published_at'])
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.published_at)
        self.assertTrue(
            PlanEvent.objects.filter(
                plan=self.plan,
                event_type='published',
            ).exists(),
        )

    def test_publish_idempotent(self):
        PlanRun.objects.create(
            plan=self.plan,
            run_number=1,
            status=PlanRunStatus.COMPLETE,
            driver_version='test-1.0',
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        first = self.client.post(f'/planning/plans/{self.plan.id}/publish/')
        second = self.client.post(f'/planning/plans/{self.plan.id}/publish/')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            PlanEvent.objects.filter(plan=self.plan, event_type='published').count(),
            1,
        )


class PortalTodayApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        self.unit11 = Location.objects.create(id=2, name='Unit 11', visible=True)
        self.sleeving = Location.objects.create(id=5, name='Sleeving', visible=True)
        self.box = Product.objects.create(
            name='Box - GRAB AND GO',
            recipe_code='BOX1',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=self.unit11.id,
            destination_container_id=self.sleeving.id,
        )
        self.plan = Plan.objects.create(
            plan_date=date(2026, 8, 2),
            location=self.sleeving,
            published_at=timezone.now(),
        )
        self.run = PlanRun.objects.create(
            plan=self.plan,
            run_number=1,
            status=PlanRunStatus.COMPLETE,
            driver_version='test-1.0',
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        PlanRequirement.objects.create(
            run=self.run,
            level=1,
            batch_number=1,
            product=self.box,
            net_required=Decimal('20'),
            gross_required=Decimal('20'),
            yield_factor=Decimal('1'),
            process_loss=Decimal('1'),
            source_location=self.unit11,
            destination_location=self.sleeving,
        )

    def test_portal_today_outbound(self):
        resp = self.client.get(
            '/planning/portal/today/'
            f'?location=Unit%2011&plan_date=2026-08-02&mode=outbound',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['location'], 'Unit 11')
        self.assertEqual(data['location_id'], self.unit11.id)
        self.assertEqual(data['mode'], 'outbound')
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['plan_id'], self.plan.id)
        self.assertEqual(item['run_id'], self.run.id)
        self.assertEqual(len(item['lines']), 1)
        self.assertEqual(item['lines'][0]['product_id'], self.box.id)
        self.assertEqual(item['lines'][0]['from_location_id'], self.unit11.id)

    def test_portal_today_inbound(self):
        resp = self.client.get(
            '/planning/portal/today/'
            f'?location={self.sleeving.id}&plan_date=2026-08-02&mode=inbound',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['mode'], 'inbound')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['lines'][0]['to_location'], 'Sleeving')

    def test_portal_today_unpublished_excluded(self):
        self.plan.published_at = None
        self.plan.save(update_fields=['published_at'])
        resp = self.client.get(
            '/planning/portal/today/?location=Unit%2011&plan_date=2026-08-02',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['items'], [])

    def test_portal_today_location_required(self):
        resp = self.client.get('/planning/portal/today/')
        self.assertEqual(resp.status_code, 422)


class PlanProgressApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='Each')
        self.spice = Location.objects.create(id=15, name='Spice Room', visible=True)
        self.mixers = Location.objects.create(id=16, name='Mixers', visible=True)
        self.spice_mix = Product.objects.create(
            name='Vegetable Samosa - 003 - Spice',
            recipe_code='SP1',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=self.spice.id,
            destination_container_id=self.mixers.id,
        )
        self.plan = Plan.objects.create(
            plan_date=date(2026, 8, 5),
            location=self.mixers,
        )
        self.run = PlanRun.objects.create(
            plan=self.plan,
            run_number=1,
            status=PlanRunStatus.COMPLETE,
            driver_version='test-1.0',
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        PlanRequirement.objects.create(
            run=self.run,
            level=1,
            batch_number=1,
            product=self.spice_mix,
            net_required=Decimal('10'),
            gross_required=Decimal('10'),
            yield_factor=Decimal('1'),
            process_loss=Decimal('1'),
            source_location=self.spice,
            destination_location=self.mixers,
        )
        self.resource = Resource.objects.create(
            id=1,
            code='SPICE1',
            name='Spice line',
            location=self.spice,
        )
        period = StockPeriod.objects.create(
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
        lot = StockLot.objects.create(
            product=self.spice_mix,
            trace_number='T1',
            origin=StockLotOrigin.PRODUCTION,
            production_date=date(2026, 8, 5),
        )
        now = timezone.now()
        for i, qty in enumerate((Decimal('5'), Decimal('5')), start=1):
            entry = StockEntry.objects.create(
                idempotency_key=f'prog-test-{i}',
                entry_type=StockEntryType.PRODUCTION_OUTPUT,
                lot=lot,
                location=self.mixers,
                counterparty_location=self.spice,
                quantity=qty,
                unit=self.unit,
                period=period,
                effective_at=now,
                recorded_at=now,
                entry_hash=f'hash-prog-{i}',
            )
            ProductionRun.objects.create(
                stock_entry=entry,
                resource=self.resource,
                base_date=date(2026, 8, 5),
                finished_at=now,
            )

    def test_progress_planned_vs_made(self):
        resp = self.client.get(f'/planning/plans/{self.plan.id}/progress/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['plan_id'], self.plan.id)
        self.assertEqual(data['run_id'], self.run.id)
        self.assertEqual(len(data['lines']), 1)
        line = data['lines'][0]
        self.assertEqual(line['product_id'], self.spice_mix.id)
        self.assertEqual(line['from_location'], 'Spice Room')
        self.assertEqual(line['planned'], '10.000000')
        self.assertEqual(line['done'], '10.000000')
        self.assertEqual(line['status'], 'complete')
        self.assertEqual(line['pct'], '100.00')
        self.assertIsNotNone(line['last_made_at'])
        dept = data['by_department'][0]
        self.assertEqual(dept['complete_count'], 1)

    def test_progress_location_filter(self):
        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/progress/?location=Spice%20Room',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['location'], 'Spice Room')
        self.assertEqual(len(data['lines']), 1)

    def test_progress_requires_complete_run(self):
        bare = Plan.objects.create(
            plan_date=date(2026, 8, 6),
            location=self.mixers,
        )
        resp = self.client.get(f'/planning/plans/{bare.id}/progress/')
        self.assertEqual(resp.status_code, 422)
