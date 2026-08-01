from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from planning.models import Plan, PlanRequirement, PlanRun, PlanRunStatus
from product.models import Category, Product, ProductClass, Range, Unit


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
        self.assertNotIn('run_id', data)
        self.assertNotIn('plan_id', data)

        by_product = {row['product']: row for row in data['lines']}
        self.assertEqual(len(by_product), 3)
        box_row = by_product['Box - GRAB AND GO']
        self.assertEqual(box_row['gross_quantity'], '50.000000')
        self.assertEqual(box_row['from_location'], 'Unit 11')
        self.assertEqual(box_row['to_location'], 'Sleeving')
        self.assertEqual(box_row['unit'], 'Each')
        self.assertNotIn('product_id', box_row)
        self.assertNotIn('from_location_id', box_row)

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
        self.assertEqual(len(data['lines']), 2)
        self.assertTrue(
            all(row['from_location'] == 'Unit 11' for row in data['lines']),
        )
        self.assertEqual(len(data['by_department']), 2)
