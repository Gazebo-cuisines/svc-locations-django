"""Incomplete MADE hold: allocation status + Dispatch balance gate."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location, LocationStockProfile
from planning.models import Resource
from product.models import Category, Product, ProductClass, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from stock_ledger.models import (
    StockBalance,
    StockLot,
    StockLotOrigin,
)
from stock_ledger.util import services
from stock_ledger.util.allocation_status import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NO_RECIPE,
    allocation_status,
)


class IncompleteAllocationHoldTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=71, name='IA Class')
        Category.objects.create(id=71, name='IA Cat')
        Range.objects.create(id=71, name='IA Range')
        self.unit = Unit.objects.create(id=71, name='Kg')
        self.sleeving = Location.objects.create(id=71, name='IA Sleeving', visible=True)
        self.dispatch = Location.objects.create(id=72, name='IA Dispatch', visible=True)
        LocationStockProfile.objects.create(
            location=self.dispatch,
            stock_identifier='STK',
            production_identifier='PROD',
            show_incomplete_stock=False,
        )
        LocationStockProfile.objects.create(
            location=self.sleeving,
            stock_identifier='STK',
            production_identifier='PROD',
            show_incomplete_stock=False,
        )
        self.resource = Resource.objects.create(
            id=71, code='IA-L1', name='IA Line 1', location=self.sleeving,
        )

        self.component = self._product('IA Component', dest=self.sleeving)
        self.fg = self._product('IA Finished', dest=self.dispatch)

        recipe = Recipe.objects.create(product=self.fg, name='IA FG Recipe')
        self.version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
        )
        # 1 kg FG needs 1 kg component
        RecipeComponent.objects.create(
            recipe_version=self.version,
            line_no=1,
            component_product=self.component,
            quantity=Decimal('1'),
            unit=self.unit,
        )

        # Component stock at Sleeving for consume
        self.comp_lot = StockLot.objects.create(
            product=self.component,
            trace_number=f'C{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        services.receipt(
            idempotency_key=f'ia-comp-{uuid4()}',
            lot=self.comp_lot,
            location_id=self.sleeving.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

    def _product(self, name, *, dest):
        return Product.objects.create(
            name=f'{name} {uuid4().hex[:6]}',
            recipe_code=f'IA{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
            unit=self.unit,
            source_container=self.sleeving,
            destination_container=dest,
        )

    def _make_fg(self, qty='10'):
        lot = StockLot.objects.create(
            product=self.fg,
            recipe_version=self.version,
            trace_number=f'F{uuid4().hex[:8]}',
            origin=StockLotOrigin.PRODUCTION,
            use_by=date(2026, 9, 1),
            production_date=date(2026, 8, 6),
        )
        entry, run = services.production_output(
            idempotency_key=f'ia-made-{uuid4()}',
            lot=lot,
            location_id=self.dispatch.id,
            counterparty_location_id=self.sleeving.id,
            quantity=Decimal(qty),
            unit_id=self.unit.id,
            resource_id=self.resource.id,
            base_date=date(2026, 8, 6),
            effective_at=timezone.now(),
        )
        return lot, entry, run

    def _consume_all(self, output_entry, qty='10'):
        return services.production_consume(
            idempotency_key=f'ia-consume-{uuid4()}',
            output_entry_id=output_entry.id,
            lot=self.comp_lot,
            location_id=self.sleeving.id,
            quantity=Decimal(qty),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

    def test_made_without_consume_is_incomplete(self):
        lot, entry, _ = self._make_fg()
        status = allocation_status(output_entry_id=entry.id)
        self.assertEqual(status['allocation_status'], STATUS_INCOMPLETE)
        self.assertGreater(status['remaining_component_count'], 0)
        self.assertTrue(status['incomplete_reasons'])

        bal = StockBalance.objects.filter(
            lot=lot, location_id=self.dispatch.id,
        ).first()
        self.assertIsNotNone(bal)
        self.assertEqual(bal.quantity, Decimal('10'))

        resp = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        self.assertEqual(resp.status_code, 200)
        lot_ids = {row['lot_id'] for row in resp.json()['data']}
        self.assertNotIn(lot.id, lot_ids)

        # Global balances (no location_id) must also honour Dispatch hide flag.
        global_resp = self.client.get('/stock/balances/')
        self.assertEqual(global_resp.status_code, 200)
        global_lots = {row['lot_id'] for row in global_resp.json()['data']}
        self.assertNotIn(lot.id, global_lots)

    def test_consume_all_makes_complete_and_visible(self):
        lot, entry, _ = self._make_fg()
        self._consume_all(entry)
        status = allocation_status(output_entry_id=entry.id)
        self.assertEqual(status['allocation_status'], STATUS_COMPLETE)
        self.assertEqual(status['remaining_component_count'], 0)

        resp = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        lot_ids = {row['lot_id'] for row in resp.json()['data']}
        self.assertIn(lot.id, lot_ids)

    def test_purchase_lot_never_hidden(self):
        purchase = StockLot.objects.create(
            product=self.fg,
            trace_number=f'P{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 10, 1),
        )
        services.receipt(
            idempotency_key=f'ia-purchase-{uuid4()}',
            lot=purchase,
            location_id=self.dispatch.id,
            quantity=Decimal('5'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.sleeving.id,
        )
        # Also an incomplete MADE so the gate is active
        self._make_fg(qty='3')

        resp = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        lot_ids = {row['lot_id'] for row in resp.json()['data']}
        self.assertIn(purchase.id, lot_ids)

    def test_show_incomplete_stock_flag_shows_held_lot(self):
        lot, _, _ = self._make_fg()
        LocationStockProfile.objects.filter(pk=self.dispatch.id).update(
            show_incomplete_stock=True,
        )
        resp = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        lot_ids = {row['lot_id'] for row in resp.json()['data']}
        self.assertIn(lot.id, lot_ids)

    def test_include_incomplete_query_override(self):
        lot, _, _ = self._make_fg()
        hidden = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        self.assertNotIn(lot.id, {r['lot_id'] for r in hidden.json()['data']})

        shown = self.client.get(
            f'/stock/balances/?location_id={self.dispatch.id}&include_incomplete=1',
        )
        self.assertIn(lot.id, {r['lot_id'] for r in shown.json()['data']})

    def test_production_list_allocation_status_filter(self):
        _, incomplete_entry, _ = self._make_fg(qty='4')
        _, complete_entry, _ = self._make_fg(qty='2')
        self._consume_all(complete_entry, qty='2')

        incomplete = self.client.get(
            f'/stock/production/?from_location_id={self.sleeving.id}'
            f'&allocation_status=incomplete',
        )
        self.assertEqual(incomplete.status_code, 200)
        ids = {row['entry_id'] for row in incomplete.json()['data']}
        self.assertIn(incomplete_entry.id, ids)
        self.assertNotIn(complete_entry.id, ids)

        complete = self.client.get(
            f'/stock/production/?from_location_id={self.sleeving.id}'
            f'&allocation_status=complete',
        )
        ids = {row['entry_id'] for row in complete.json()['data']}
        self.assertIn(complete_entry.id, ids)
        self.assertNotIn(incomplete_entry.id, ids)

        row = next(
            r for r in incomplete.json()['data'] if r['entry_id'] == incomplete_entry.id
        )
        self.assertEqual(row['allocation_status'], STATUS_INCOMPLETE)
        self.assertTrue(row['incomplete_reasons'])

    def test_no_recipe_is_complete_and_visible(self):
        bare = self._product('IA Bare FG', dest=self.dispatch)
        lot = StockLot.objects.create(
            product=bare,
            trace_number=f'B{uuid4().hex[:8]}',
            origin=StockLotOrigin.PRODUCTION,
            use_by=date(2026, 9, 1),
        )
        entry, _ = services.production_output(
            idempotency_key=f'ia-bare-{uuid4()}',
            lot=lot,
            location_id=self.dispatch.id,
            counterparty_location_id=self.sleeving.id,
            quantity=Decimal('7'),
            unit_id=self.unit.id,
            resource_id=self.resource.id,
            base_date=date(2026, 8, 6),
            effective_at=timezone.now(),
        )
        status = allocation_status(output_entry_id=entry.id)
        self.assertEqual(status['allocation_status'], STATUS_NO_RECIPE)

        resp = self.client.get(f'/stock/balances/?location_id={self.dispatch.id}')
        self.assertIn(lot.id, {r['lot_id'] for r in resp.json()['data']})

    def test_allocation_status_endpoint(self):
        _, entry, _ = self._make_fg(qty='10')
        resp = self.client.get(
            f'/stock/production/{entry.id}/allocation-status/'
            f'?location_id={self.sleeving.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['allocation_status'], STATUS_INCOMPLETE)
        self.assertTrue(data['incomplete_reasons'])
        self.assertTrue(data['remaining_lines'])
        self.assertEqual(data['made_quantity'], '10.000000')
        self.assertEqual(len(data['components']), 1)
        self.assertEqual(
            Decimal(data['components'][0]['remaining_quantity']),
            Decimal('10'),
        )
