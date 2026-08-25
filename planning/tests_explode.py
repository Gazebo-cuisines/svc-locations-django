from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from locations.models import Location
from planning.models import Plan, PlanLine, PlanLineSource, PlanRequirement
from planning.services.explode import run_explode
from product.models import Category, Product, ProductClass, ProductYield, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from recipe.utils import scaled_child_net
from stock_ledger.models import StockUnitConversion
from stock_ledger.util.conversions import seed_global_unit_conversions


class ScaledChildNetTests(SimpleTestCase):
    def test_pack_per_unit(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('5000'), Decimal('12'), bom_sum=Decimal('25'),
            ),
            Decimal('60000'),
        )

    def test_belt_piece_weight(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('60000'), Decimal('80.2'), bom_sum=Decimal('81.2'),
            ),
            Decimal('4812000.0'),
        )

    def test_mixer_batch(self):
        got = scaled_child_net(
            Decimal('4812000'),
            Decimal('96000'),
            batch_quantity=Decimal('186080'),
            bom_sum=Decimal('186080'),
        )
        self.assertEqual(got, Decimal('4812000') * Decimal('96000') / Decimal('186080'))

    def test_spice_null_batch_uses_bom_sum(self):
        got = scaled_child_net(
            Decimal('285000'),
            Decimal('2000'),
            batch_quantity=None,
            bom_sum=Decimal('11040'),
        )
        self.assertEqual(got, Decimal('285000') * Decimal('2000') / Decimal('11040'))

    def test_min_batch_not_scaled(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('50'),
                Decimal('2'),
                batch_quantity=Decimal('25'),
                bom_sum=Decimal('2'),
            ),
            Decimal('100'),
        )

    def test_small_steam_process_batch(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('160000'),
                Decimal('500'),
                bom_sum=Decimal('500'),
                process_batch=True,
            ),
            Decimal('160000'),
        )

    def test_small_spice_process_batch(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('20334'),
                Decimal('251'),
                bom_sum=Decimal('251'),
                process_batch=True,
            ),
            Decimal('20334'),
        )

    def test_meal_fill_stays_per_unit(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('8000'),
                Decimal('160'),
                bom_sum=Decimal('401.2'),
            ),
            Decimal('1280000'),
        )


class ExplodeBatchBomTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='grams')
        loc = Location.objects.create(id=1, name='Mixers', visible=True)
        self.mixer = self._product('Mixer', loc)
        self.potato = self._product('Potato', loc)
        ProductYield.objects.create(
            product=self.mixer, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        ProductYield.objects.create(
            product=self.potato, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        recipe = Recipe.objects.create(product=self.mixer, name='Mx')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
            batch_quantity=Decimal('186080'),
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=self.potato,
            quantity=Decimal('96000'),
            unit=self.unit,
        )
        self.plan = Plan.objects.create(plan_date=date(2026, 8, 21), location=loc)
        PlanLine.objects.create(
            plan=self.plan,
            product=self.mixer,
            quantity=Decimal('4812000'),
            unit=self.unit,
            source=PlanLineSource.MANUAL,
        )

    def _product(self, name, loc):
        return Product.objects.create(
            name=name,
            recipe_code=name[:8],
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=loc,
            destination_container=loc,
        )

    def test_mixer_child_scaled_by_batch(self):
        run = run_explode(self.plan.id)
        potato = PlanRequirement.objects.get(
            run=run, product_id=self.potato.id,
        )
        expected = Decimal('4812000') * Decimal('96000') / Decimal('186080')
        self.assertEqual(
            potato.net_required,
            expected.quantize(Decimal('0.000001')),
        )
        self.assertLess(potato.net_required, Decimal('10000000'))

        mixer = PlanRequirement.objects.get(run=run, product_id=self.mixer.id)
        self.assertEqual(mixer.calc_json['kind'], 'demand')
        self.assertEqual(mixer.calc_json['steps'][0]['op'], 'gross')
        self.assertEqual(mixer.calc_json['steps'][0]['formula'], 'net / recipe_yield')
        self.assertIn('recipe yield 100%', mixer.calc_json['steps'][0]['from'])
        stock = next(s for s in mixer.calc_json['steps'] if s['op'] == 'stock_net')
        self.assertTrue(stock['skipped'])

        scale = next(s for s in potato.calc_json['steps'] if s['op'] == 'scale_bom')
        self.assertEqual(scale['formula'], 'parent_gross × bom_qty / batch')
        self.assertEqual(scale['from'], '4812000 × 96000 / 186080')
        self.assertEqual(
            Decimal(scale['to']).quantize(Decimal('0.000001')),
            potato.net_required,
        )
        self.assertEqual(potato.calc_json['kind'], 'child')
        self.assertEqual(potato.calc_json['inputs']['recipe_version_number'], None)
        self.assertEqual(potato.calc_json['inputs']['source_recipe_version_number'], 1)
        self.assertEqual(mixer.calc_json['inputs']['recipe_version_number'], 1)
        self.assertEqual(mixer.calc_json['inputs']['source_recipe_version_number'], 1)
        self.assertEqual(run.stamp_json['what'], 'explode')
        self.assertEqual(run.stamp_json['driver'], 'explode-1.4')
        self.assertEqual(run.stamp_json['line_count'], 1)

        resp = self.client.get(
            f'/planning/plans/{self.plan.id}/runs/{run.id}/requirements/',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['stamp']['what'], 'explode')
        by_name = {row['product_name']: row for row in data['items']}
        self.assertIn('Mixer', by_name)
        self.assertIn('Potato', by_name)
        self.assertEqual(by_name['Potato']['unit_name'], 'grams')
        self.assertEqual(by_name['Potato']['category_name'], 'Meals')
        self.assertEqual(by_name['Potato']['product_class_name'], 'Finished')
        self.assertEqual(by_name['Potato']['calc_json']['kind'], 'child')
        self.assertEqual(by_name['Mixer']['recipe_version_number'], 1)
        self.assertEqual(by_name['Mixer']['source_recipe_version_number'], 1)
        self.assertIsNone(by_name['Potato']['recipe_version_number'])
        self.assertEqual(by_name['Potato']['source_recipe_version_number'], 1)
        self.assertEqual(by_name['Potato']['source_product_id'], self.mixer.id)
        self.assertEqual(by_name['Mixer']['process_loss'], '1')


class ExplodePastryUomTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.sheet = Unit.objects.create(id=1, name='unit')
        self.kg = Unit.objects.create(id=2, name='Kg')
        loc = Location.objects.create(id=1, name='Belt', visible=True)
        self.belt = self._product('Belt FG', self.sheet, loc, 'BELT-01')
        self.pastry = self._product('PASTRY-01 LARGE', self.kg, loc, 'PASTRY-01')
        ProductYield.objects.create(
            product=self.belt, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        ProductYield.objects.create(
            product=self.pastry, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        recipe = Recipe.objects.create(product=self.belt, name='Belt')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=self.pastry,
            quantity=Decimal('1'),
            unit=self.sheet,
        )
        self.plan = Plan.objects.create(plan_date=date(2026, 8, 10), location=loc)
        PlanLine.objects.create(
            plan=self.plan,
            product=self.belt,
            quantity=Decimal('450'),
            unit=self.sheet,
            source=PlanLineSource.MANUAL,
        )
        seed_global_unit_conversions()

    def _product(self, name, unit, loc, code):
        return Product.objects.create(
            name=name,
            recipe_code=code,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=unit,
            source_container=loc,
            destination_container=loc,
        )

    def test_sheets_convert_to_kg(self):
        kg_per_sheet = (Decimal('10') / Decimal('323')).quantize(Decimal('0.000001'))
        StockUnitConversion.objects.create(
            unit=self.sheet,
            product=self.pastry,
            to_kg=kg_per_sheet,
            source='product_packaging',
        )
        run = run_explode(self.plan.id)
        pastry = PlanRequirement.objects.get(run=run, product_id=self.pastry.id)
        expected = (Decimal('450') * kg_per_sheet).quantize(Decimal('0.000001'))
        self.assertEqual(pastry.net_required, expected)
        self.assertNotEqual(pastry.net_required, Decimal('450'))
        convert = next(
            s for s in pastry.calc_json['steps'] if s['op'] == 'convert_uom'
        )
        self.assertFalse(convert.get('skipped'))
        self.assertIsNone(pastry.calc_json.get('warnings'))

    def test_missing_conversion_keeps_sheets_and_warns(self):
        run = run_explode(self.plan.id)
        pastry = PlanRequirement.objects.get(run=run, product_id=self.pastry.id)
        self.assertEqual(pastry.net_required, Decimal('450'))
        convert = next(
            s for s in pastry.calc_json['steps'] if s['op'] == 'convert_uom'
        )
        self.assertTrue(convert['skipped'])
        self.assertEqual(convert['reason'], 'missing_uom_conversion')
        self.assertEqual(pastry.calc_json['warnings'], ['missing_uom_conversion'])


class ExplodeSmallProcessBatchTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='grams')
        loc = Location.objects.create(id=1, name='Steam', visible=True)
        self.steam = self._product('Steamed Peas - 207 - Steaming', loc, 'GFF207R - St')
        self.peas = self._product('PEAS (FROZEN)', loc, 'VEGFRO-01')
        ProductYield.objects.create(
            product=self.steam, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        ProductYield.objects.create(
            product=self.peas, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        recipe = Recipe.objects.create(product=self.steam, name='Peas')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=self.peas,
            quantity=Decimal('500'),
            unit=self.unit,
        )
        self.plan = Plan.objects.create(plan_date=date(2026, 8, 20), location=loc)
        PlanLine.objects.create(
            plan=self.plan,
            product=self.steam,
            quantity=Decimal('160000'),
            unit=self.unit,
            source=PlanLineSource.MANUAL,
        )

    def _product(self, name, loc, code):
        return Product.objects.create(
            name=name,
            recipe_code=code,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=loc,
            destination_container=loc,
        )

    def test_steam_peas_scales_as_batch(self):
        run = run_explode(self.plan.id)
        peas = PlanRequirement.objects.get(run=run, product_id=self.peas.id)
        self.assertEqual(peas.net_required, Decimal('160000'))
        self.assertNotEqual(peas.net_required, Decimal('80000000'))
        scale = next(s for s in peas.calc_json['steps'] if s['op'] == 'scale_bom')
        self.assertIn('/ 500', scale['from'])
