"""Chunk 6: chain-net numeric + API smoke."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from locations.models import Location
from planning.models import Plan, PlanLine, PlanLineSource
from planning.services.chain_net import _apply_min_batch, chain_net_plan
from product.models import Category, Product, ProductClass, ProductYield, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from stock_ledger.models import StockLot, StockLotOrigin
from stock_ledger.util import services as stock_services


class ChainNetMinBatchUnitTests(TestCase):
    def test_ceil_shortfall_to_batch(self):
        to_make, batch = _apply_min_batch(Decimal('10'), Decimal('25'))
        self.assertEqual(to_make, Decimal('25'))
        self.assertEqual(batch, Decimal('25'))

    def test_exact_batch_unchanged(self):
        to_make, batch = _apply_min_batch(Decimal('50'), Decimal('25'))
        self.assertEqual(to_make, Decimal('50'))
        self.assertEqual(batch, Decimal('25'))

    def test_no_batch_passthrough(self):
        to_make, batch = _apply_min_batch(Decimal('10'), None)
        self.assertEqual(to_make, Decimal('10'))
        self.assertIsNone(batch)


class ChainNetPlanTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='Each')
        self.dispatch = Location.objects.create(id=6, name='Dispatch', visible=True)
        self.sleeving = Location.objects.create(id=5, name='Sleeving', visible=True)
        self.spice = Location.objects.create(id=15, name='Spice Room', visible=True)

        self.fg = Product.objects.create(
            name='Veg Samosa FG',
            recipe_code='FG1',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=self.sleeving,
            destination_container=self.dispatch,
        )
        ProductYield.objects.create(
            product=self.fg,
            yield_factor=Decimal('1'),
            yield_factor_auto=Decimal('1'),
        )
        self.spice_sku = Product.objects.create(
            name='Spice Pack',
            recipe_code='SP1',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=self.spice,
            destination_container=self.sleeving,
        )
        ProductYield.objects.create(
            product=self.spice_sku,
            yield_factor=Decimal('1'),
            yield_factor_auto=Decimal('1'),
        )
        self.raw = Product.objects.create(
            name='Balti Paste',
            recipe_code='RAW1',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=self.spice,
            destination_container=self.spice,
        )
        ProductYield.objects.create(
            product=self.raw,
            yield_factor=Decimal('1'),
            yield_factor_auto=Decimal('1'),
        )

        fg_recipe = Recipe.objects.create(product=self.fg, name='FG')
        fg_ver = RecipeVersion.objects.create(
            recipe=fg_recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
        )
        RecipeComponent.objects.create(
            recipe_version=fg_ver,
            line_no=1,
            component_product=self.spice_sku,
            quantity=Decimal('1'),
            unit=self.unit,
        )

        spice_recipe = Recipe.objects.create(product=self.spice_sku, name='Spice')
        spice_ver = RecipeVersion.objects.create(
            recipe=spice_recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
            batch_quantity=Decimal('25'),
        )
        RecipeComponent.objects.create(
            recipe_version=spice_ver,
            line_no=1,
            component_product=self.raw,
            quantity=Decimal('2'),
            unit=self.unit,
        )

        lot = StockLot.objects.create(
            product=self.fg,
            trace_number='20215',
            origin=StockLotOrigin.PRODUCTION,
            production_date=date(2026, 8, 4),
        )
        stock_services.receipt(
            idempotency_key=f'chain-net-test-{uuid4()}',
            lot=lot,
            location_id=self.dispatch.id,
            quantity=Decimal('10'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

        self.plan = Plan.objects.create(
            plan_date=date(2026, 8, 5),
            location=self.dispatch,
        )
        PlanLine.objects.create(
            plan=self.plan,
            product=self.fg,
            quantity=Decimal('50'),
            unit=self.unit,
            source=PlanLineSource.MANUAL,
        )

    def test_dispatch_stock_and_spice_min_batch(self):
        result = chain_net_plan(self.plan.id)
        root = result['items'][0]

        self.assertEqual(Decimal(root['demand']), Decimal('50'))
        self.assertEqual(Decimal(root['stock']), Decimal('10'))
        self.assertEqual(Decimal(root['to_make']), Decimal('40'))
        self.assertEqual(root['stock_lots'][0]['trace_number'], '20215')
        self.assertIn('lot:', root['stock_lots'][0]['stock_ref'])
        self.assertIn('Dispatch', root['explanation'])
        self.assertIn('planning 40', root['summary'])

        self.assertEqual(len(root['children']), 1)
        spice = root['children'][0]
        self.assertEqual(spice['product_id'], self.spice_sku.id)
        self.assertEqual(Decimal(spice['demand']), Decimal('40'))
        self.assertEqual(Decimal(spice['shortfall']), Decimal('40'))
        self.assertEqual(Decimal(spice['min_batch']), Decimal('25'))
        self.assertEqual(Decimal(spice['to_make']), Decimal('50'))
        self.assertIn('Min batch', spice['explanation'])

        raw = spice['children'][0]
        self.assertEqual(raw['product_id'], self.raw.id)
        self.assertEqual(Decimal(raw['demand']), Decimal('100'))

    def test_process_stock_at_destination(self):
        """Finished process WIP sits at destination, not source."""
        lot = StockLot.objects.create(
            product=self.spice_sku,
            trace_number='SPICE-DEST',
            origin=StockLotOrigin.PRODUCTION,
            production_date=date(2026, 8, 4),
        )
        stock_services.receipt(
            idempotency_key=f'chain-net-spice-{uuid4()}',
            lot=lot,
            location_id=self.sleeving.id,
            quantity=Decimal('15'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

        result = chain_net_plan(self.plan.id)
        spice = result['items'][0]['children'][0]
        self.assertEqual(Decimal(spice['stock']), Decimal('15'))
        self.assertEqual(Decimal(spice['demand']), Decimal('40'))
        self.assertEqual(Decimal(spice['shortfall']), Decimal('25'))
        self.assertEqual(Decimal(spice['to_make']), Decimal('25'))
        self.assertEqual(spice['stock_lots'][0]['location_id'], self.sleeving.id)

    def test_chain_net_api(self):
        resp = self.client.post(
            f'/planning/plans/{self.plan.id}/chain-net/',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()['data']
        self.assertEqual(payload['plan_id'], self.plan.id)
        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['items'][0]['to_make'], '40.000000')
        self.assertTrue(payload['product_lines'])
        self.assertIn('stock_lots', payload['product_lines'][0])
        self.assertIn('unit_name', payload['product_lines'][0])
