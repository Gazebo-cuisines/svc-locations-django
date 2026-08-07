"""Recall / product genealogy APIs."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import (
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
    StockUnitConversion,
)
from stock_ledger.util import services


class RecallApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=81, name='Recall Class')
        Category.objects.create(id=81, name='Recall Cat')
        Range.objects.create(id=81, name='Recall Range')
        self.unit = Unit.objects.create(id=81, name='Kg')
        StockUnitConversion.objects.get_or_create(
            unit=self.unit,
            product=None,
            defaults={'to_kg': Decimal('1'), 'source': 'global'},
        )
        self.wh = Location.objects.create(id=81, name='Recall WH', visible=True)
        self.kitchen = Location.objects.create(id=82, name='Recall Kitchen', visible=True)
        self.supplier = Location.objects.create(id=83, name='Recall Supplier Co', visible=True)
        self.use_by = date(2026, 9, 15)
        self.fg = Product.objects.create(
            name=f'Recall FG {uuid4().hex[:8]}',
            recipe_code=f'RF{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.kitchen,
        )
        self.comp = Product.objects.create(
            name=f'Recall Comp {uuid4().hex[:8]}',
            recipe_code=f'RC{uuid4().hex[:6]}',
            product_class_id=81,
            category_id=81,
            range_id=81,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.kitchen,
        )
        StockPeriod.objects.get_or_create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            defaults={'status': StockPeriodStatus.OPEN},
        )
        self.client = Client()

        self.comp_lot = StockLot.objects.create(
            product=self.comp,
            trace_number=f'C{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 10, 1),
        )
        services.receipt(
            idempotency_key=f'recall-comp-{uuid4()}',
            lot=self.comp_lot,
            location_id=self.wh.id,
            quantity=Decimal('50'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
            po_number='PO-RECALL-1',
        )

        self.fg_lot_linked = StockLot.objects.create(
            product=self.fg,
            trace_number=f'FA{uuid4().hex[:8]}',
            origin=StockLotOrigin.PRODUCTION,
            production_date=date(2026, 8, 10),
            use_by=self.use_by,
        )
        self.fg_lot_orphan = StockLot.objects.create(
            product=self.fg,
            trace_number=f'FB{uuid4().hex[:8]}',
            origin=StockLotOrigin.PRODUCTION,
            production_date=date(2026, 8, 11),
            use_by=self.use_by,
        )
        services.production(
            idempotency_key=f'recall-prod-{uuid4()}',
            output_lot=self.fg_lot_linked,
            output_location_id=self.kitchen.id,
            output_quantity=Decimal('10'),
            output_unit_id=self.unit.id,
            inputs=[{
                'lot': self.comp_lot,
                'location_id': self.wh.id,
                'quantity': Decimal('5'),
                'unit_id': self.unit.id,
                'genealogy_quantity_base': Decimal('5'),
            }],
            effective_at=timezone.now(),
        )
        # Second batch same use_by, no genealogy edges.
        services.receipt(
            idempotency_key=f'recall-orphan-{uuid4()}',
            lot=self.fg_lot_orphan,
            location_id=self.kitchen.id,
            quantity=Decimal('3'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

    def test_recall_returns_all_lots_same_use_by(self):
        resp = self.client.get(
            f'/stock/recall/?product_id={self.fg.id}&use_by={self.use_by.isoformat()}'
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'success')
        data = body['data']
        self.assertEqual(data['lot_count'], 2)
        self.assertEqual(data['product']['id'], self.fg.id)
        self.assertEqual(data['use_by'], self.use_by.isoformat())
        by_id = {row['lot']['id']: row for row in data['lots']}
        self.assertIn(self.fg_lot_linked.id, by_id)
        self.assertIn(self.fg_lot_orphan.id, by_id)
        linked = by_id[self.fg_lot_linked.id]
        orphan = by_id[self.fg_lot_orphan.id]
        self.assertTrue(linked['has_genealogy'])
        self.assertTrue(linked['genealogy']['backward'])
        self.assertEqual(
            linked['genealogy']['backward'][0]['product_id'],
            self.comp.id,
        )
        self.assertIn('product_name', linked['genealogy']['backward'][0])
        graph = linked['genealogy']['graph']
        self.assertTrue(graph['nodes'])
        self.assertTrue(graph['edges'])
        node_ids = {n['id'] for n in graph['nodes']}
        self.assertIn(f'lot-{self.fg_lot_linked.id}', node_ids)
        self.assertIn(f'lot-{self.comp_lot.id}', node_ids)
        focus = next(
            n for n in graph['nodes'] if n['id'] == f'lot-{self.fg_lot_linked.id}'
        )
        self.assertEqual(focus['data']['role'], 'focus')
        gene_edges = [
            e for e in graph['edges']
            if e.get('data', {}).get('kind') == 'genealogy'
        ]
        self.assertTrue(gene_edges)
        edge = gene_edges[0]
        self.assertEqual(edge['source'], f'lot-{self.comp_lot.id}')
        self.assertEqual(edge['target'], f'lot-{self.fg_lot_linked.id}')
        types = {n['type'] for n in graph['nodes']}
        self.assertIn('supplier', types)
        self.assertIn('location', types)
        supplier_node = next(n for n in graph['nodes'] if n['type'] == 'supplier')
        self.assertEqual(supplier_node['data']['location_id'], self.supplier.id)
        self.assertFalse(orphan['has_genealogy'])
        self.assertEqual(orphan['genealogy']['backward'], [])
        self.assertEqual(orphan['genealogy']['forward'], [])
        self.assertEqual(orphan['genealogy']['graph']['nodes'][0]['data']['role'], 'focus')
        # Orphan has no gene edges; may still have location/balance nodes
        self.assertFalse(
            any(
                e.get('data', {}).get('kind') == 'genealogy'
                for e in orphan['genealogy']['graph']['edges']
            )
        )

    def test_recall_empty_lots_ok(self):
        resp = self.client.get(
            f'/stock/recall/?product_id={self.fg.id}&use_by=2099-01-01'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['lot_count'], 0)
        self.assertEqual(data['lots'], [])

    def test_recall_404_and_400(self):
        resp = self.client.get('/stock/recall/?product_id=999999&use_by=2026-09-15')
        self.assertEqual(resp.status_code, 404)
        resp = self.client.get(f'/stock/recall/?product_id={self.fg.id}&use_by=not-a-date')
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(f'/stock/recall/?product_id={self.fg.id}')
        self.assertEqual(resp.status_code, 400)

    def test_product_genealogy_index(self):
        resp = self.client.get(f'/stock/products/{self.fg.id}/genealogy/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertGreaterEqual(data['lot_count'], 2)
        by_id = {row['lot']['id']: row for row in data['lots']}
        self.assertTrue(by_id[self.fg_lot_linked.id]['has_genealogy'])
        self.assertTrue(by_id[self.fg_lot_linked.id]['genealogy']['backward'])
        self.assertFalse(by_id[self.fg_lot_orphan.id]['has_genealogy'])

        light = self.client.get(
            f'/stock/products/{self.fg.id}/genealogy/?with_trees=0'
        )
        self.assertEqual(light.status_code, 200)
        light_data = light.json()['data']
        self.assertFalse(light_data['with_trees'])
        light_by = {row['lot']['id']: row for row in light_data['lots']}
        self.assertTrue(light_by[self.fg_lot_linked.id]['has_genealogy'])
        self.assertGreater(light_by[self.fg_lot_linked.id]['edge_count'], 0)
        self.assertNotIn('genealogy', light_by[self.fg_lot_linked.id])

    def test_product_genealogy_404(self):
        resp = self.client.get('/stock/products/999999/genealogy/')
        self.assertEqual(resp.status_code, 404)
