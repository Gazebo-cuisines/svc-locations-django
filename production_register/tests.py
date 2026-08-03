from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from locations.models import Location, LocationStockProfile
from planning.models import Plan, PlanLine
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductShelfLife,
    Range,
    Unit,
)
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from stock_ledger.models import (
    StockBalance,
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
    StockUnitConversion,
)
from stock_ledger.util import services as stock_services

from production_register.models import ProductionStation


class ProductionRegisterContractTests(TestCase):
    def setUp(self):
        tag = uuid4().hex[:6]
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='Kg')
        StockUnitConversion.objects.create(
            unit=self.unit,
            product=None,
            to_kg=Decimal('1'),
            source='global',
        )

        self.high_risk = Location.objects.create(id=4, name='High Risk', visible=True)
        self.sleeving = Location.objects.create(id=5, name='Sleeving', visible=True)
        LocationStockProfile.objects.create(
            location=self.high_risk,
            stock_identifier='STKHIGHRISK',
            production_identifier='PRODHIGHRISK',
            use_by_modifier=0,
            extends_component_use_by=False,
        )

        self.finished = Product.objects.create(
            name=f'Onion Bhaji Pack {tag}',
            recipe_code=f'OB{tag}',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=self.high_risk,
            destination_container=self.sleeving,
        )
        self.ingredient = Product.objects.create(
            name=f'Bhaji Mix {tag}',
            recipe_code=f'MX{tag}',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=self.high_risk,
            destination_container=self.high_risk,
        )
        ProductShelfLife.objects.create(
            product=self.finished,
            shelf_life_days=7,
            force_production_date=False,
            force_use_by=False,
        )

        self.recipe = Recipe.objects.create(product=self.finished, name='OB recipe')
        self.old_version = RecipeVersion.objects.create(
            recipe=self.recipe,
            version_number=1,
            status=RecipeVersionStatus.RETIRED,
            process_loss=Decimal('1'),
            batch_quantity=Decimal('10'),
            batch_unit=self.unit,
        )
        RecipeComponent.objects.create(
            recipe_version=self.old_version,
            line_no=1,
            component_product=self.ingredient,
            quantity=Decimal('1'),
            unit=self.unit,
        )
        self.active_version = RecipeVersion.objects.create(
            recipe=self.recipe,
            version_number=2,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
            batch_quantity=Decimal('10'),
            batch_unit=self.unit,
        )
        RecipeComponent.objects.create(
            recipe_version=self.active_version,
            line_no=1,
            component_product=self.ingredient,
            quantity=Decimal('2'),
            unit=self.unit,
        )

        self.hr_station = ProductionStation.objects.create(
            code='high_risk',
            name='High Risk',
            location=self.high_risk,
            default_output_location=self.sleeving,
            default_consume_location=self.high_risk,
            is_active=True,
        )
        self.slvg_station = ProductionStation.objects.create(
            code='sleeving',
            name='Sleeving',
            location=self.sleeving,
            default_output_location=self.sleeving,
            default_consume_location=self.sleeving,
            is_active=True,
        )

        StockPeriod.objects.create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            status=StockPeriodStatus.OPEN,
        )

        self.ing_lot = StockLot.objects.create(
            product=self.ingredient,
            trace_number=f'ING-{tag}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 7, 1),
            use_by=date(2026, 12, 31),
        )
        stock_services.receipt(
            idempotency_key=f'receipt-{tag}',
            lot=self.ing_lot,
            location_id=self.high_risk.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

    def test_stations_list(self):
        resp = self.client.get('/production/stations/')
        self.assertEqual(resp.status_code, 200)
        codes = {s['code'] for s in resp.json()['data']}
        self.assertEqual(codes, {'high_risk', 'sleeving'})

    def test_create_run_auto_use_by_and_active_recipe(self):
        today = timezone.localdate()
        resp = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'high_risk',
                'product_id': self.finished.id,
                'quantity_made': '20',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        run = resp.json()['data']
        self.assertEqual(run['status'], 'draft')
        self.assertEqual(run['recipe_version_id'], self.active_version.id)
        self.assertEqual(run['recipe_source'], 'active_latest')
        self.assertEqual(run['production_date'], today.isoformat())
        self.assertEqual(run['use_by'], (today + timedelta(days=7)).isoformat())

    def test_create_run_pins_plan_line_recipe(self):
        plan = Plan.objects.create(
            plan_date=timezone.localdate(),
            location=self.high_risk,
        )
        line = PlanLine.objects.create(
            plan=plan,
            product=self.finished,
            quantity=Decimal('20'),
            unit=self.unit,
            recipe_version=self.old_version,
        )
        resp = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'high_risk',
                'product_id': self.finished.id,
                'quantity_made': '20',
                'unit_id': self.unit.id,
                'plan_line_id': line.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        run = resp.json()['data']
        self.assertEqual(run['recipe_version_id'], self.old_version.id)
        self.assertEqual(run['recipe_source'], 'plan_line')

    def test_preview_bom_shows_live_lots(self):
        create = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'high_risk',
                'product_id': self.finished.id,
                'quantity_made': '20',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        run_id = create.json()['data']['id']
        resp = self.client.get(f'/production/runs/{run_id}/preview-consume/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['recipe_version_id'], self.active_version.id)
        self.assertEqual(len(data['ingredients']), 1)
        ing = data['ingredients'][0]
        # made 20 / batch 10 * component 2 = 4
        self.assertEqual(ing['needed_qty'], '4.000000')
        self.assertEqual(len(ing['lots']), 1)
        self.assertEqual(ing['lots'][0]['lot_id'], self.ing_lot.id)
        self.assertEqual(ing['lots'][0]['quantity_on_hand'], '100.000000')

    def test_post_minuses_stock_high_risk_to_sleeving(self):
        create = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'high_risk',
                'product_id': self.finished.id,
                'quantity_made': '20',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        run_id = create.json()['data']['id']

        put = self.client.put(
            f'/production/runs/{run_id}/consumptions/',
            data={
                'consumptions': [
                    {
                        'component_product_id': self.ingredient.id,
                        'lot_id': self.ing_lot.id,
                        'quantity': '4',
                        'unit_id': self.unit.id,
                    }
                ]
            },
            content_type='application/json',
        )
        self.assertEqual(put.status_code, 200, put.content)

        post = self.client.post(
            f'/production/runs/{run_id}/post/',
            data={'idempotency_key': f'post-hr-{run_id}'},
            content_type='application/json',
        )
        self.assertEqual(post.status_code, 201, post.content)
        payload = post.json()['data']
        self.assertEqual(payload['run']['status'], 'posted')
        self.assertIsNotNone(payload['run']['output_stock_entry_id'])
        self.assertIsNotNone(payload['consumptions'][0]['stock_entry_id'])

        bal = StockBalance.objects.get(
            lot_id=self.ing_lot.id,
            location_id=self.high_risk.id,
        )
        self.assertEqual(bal.quantity, Decimal('96'))

        out_bal = StockBalance.objects.filter(
            location_id=self.sleeving.id,
            lot__product_id=self.finished.id,
        ).first()
        self.assertIsNotNone(out_bal)
        self.assertEqual(out_bal.quantity, Decimal('20'))

    def test_incomplete_bom_blocked(self):
        create = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'high_risk',
                'product_id': self.finished.id,
                'quantity_made': '20',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        run_id = create.json()['data']['id']
        self.client.put(
            f'/production/runs/{run_id}/consumptions/',
            data={
                'consumptions': [
                    {
                        'component_product_id': self.ingredient.id,
                        'lot_id': self.ing_lot.id,
                        'quantity': '1',
                        'unit_id': self.unit.id,
                    }
                ]
            },
            content_type='application/json',
        )
        post = self.client.post(
            f'/production/runs/{run_id}/post/',
            data={'idempotency_key': f'post-short-{run_id}'},
            content_type='application/json',
        )
        self.assertEqual(post.status_code, 400)
        self.assertEqual(post.json()['data']['code'], 'INCOMPLETE_BOM')

    def test_sleeving_station_create(self):
        resp = self.client.post(
            '/production/runs/',
            data={
                'station_code': 'sleeving',
                'product_id': self.finished.id,
                'quantity_made': '10',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['data']['station_code'], 'sleeving')
        self.assertEqual(resp.json()['data']['from_location_id'], self.sleeving.id)
